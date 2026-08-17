"""Paired evaluation statistics used by screening and promotion gates."""

from __future__ import annotations

import math
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
from collections import Counter
from typing import Literal, Mapping

import torch


_OUTCOMES_V2 = frozenset({"win", "loss", "draw", "fault"})
_ARMS_V2 = frozenset({"candidate", "baseline"})
_POLICY_ROLES_V3 = frozenset({"theta0", "candidate"})
_EVIDENCE_KINDS_V2 = frozenset({"measured"})
_HEX64_V2 = frozenset("0123456789abcdef")
_READINESS_OPPONENTS_V3 = 6
_READINESS_SEATS_V3 = (0, 1)
_READINESS_REPETITIONS_V3 = tuple(range(8))


def validate_evidence_attestation_v2(payload: Mapping[str, object]) -> None:
    """Validate untrusted JSON evidence before it can reach an inference path."""
    required = {"engine_seed_supported", "replay_verified", "run_attestation", "seed_attestation", "evidence_kind"}
    if not required.issubset(payload):
        raise ValueError("evaluation evidence is missing required attestation fields")
    if type(payload["engine_seed_supported"]) is not bool or type(payload["replay_verified"]) is not bool:
        raise ValueError("engine/replay capability must be JSON booleans")
    if payload["evidence_kind"] not in _EVIDENCE_KINDS_V2:
        raise ValueError("synthetic/unit-only evidence cannot produce inference")
    if any(type(payload[name]) is not str or not payload[name] or payload[name].lower() in {"synthetic", "unit-only"} for name in ("run_attestation", "seed_attestation")):
        raise ValueError("evaluation evidence requires measured run and seed attestations")


def _validate_identity_v2(identity: Mapping[str, object]) -> None:
    required = {
        "opponent_id", "opponent_policy_version", "opponent_deck_fingerprint", "seat",
        "environment_seed", "agent_sampling_seed", "retry_index",
    }
    if set(identity) != required:
        raise ValueError("canonical game identity has an invalid closed field set")
    if type(identity["opponent_id"]) is not str or not identity["opponent_id"]:
        raise ValueError("canonical game identity opponent_id is invalid")
    for name in ("opponent_policy_version", "opponent_deck_fingerprint"):
        value = identity[name]
        if type(value) is not str or len(value) != 64 or any(char not in _HEX64_V2 for char in value):
            raise ValueError("canonical game identity hash is invalid")
    if type(identity["seat"]) is not int or identity["seat"] not in (0, 1) or any(type(identity[name]) is not int or identity[name] < 0 for name in ("environment_seed", "agent_sampling_seed", "retry_index")):
        raise ValueError("canonical game identity numeric fields are invalid")


def _validate_fault_provenance_v2(provenance: object) -> None:
    """Require a serializable fault explanation for every failed attempt."""
    if not isinstance(provenance, MappingABC) or not provenance:
        raise ValueError("fault outcome requires mapping fault provenance")
    exception_class = provenance.get("exception_class")
    if type(exception_class) is not str or not exception_class:
        raise ValueError("fault outcome requires fault provenance exception_class")


@dataclass(frozen=True, slots=True)
class IndependentEvaluationRecordV2:
    """One independently randomized game, never a counterfactual pair."""

    arm: Literal["candidate", "baseline"]
    outcome: Literal["win", "loss", "draw", "fault"]
    seat: int
    opponent_family: str
    canonical_game_identity: Mapping[str, object]
    record_hash: str
    engine_seed_supported: bool
    replay_verified: bool
    run_attestation: str
    seed_attestation: str
    fault_provenance: Mapping[str, object] | None

    def __post_init__(self) -> None:
        if self.arm not in _ARMS_V2 or self.outcome not in _OUTCOMES_V2:
            raise ValueError("independent record arm/outcome is invalid")
        if type(self.seat) is not int or self.seat not in (0, 1) or not self.opponent_family:
            raise ValueError("independent record seat/opponent_family is invalid")
        _validate_identity_v2(self.canonical_game_identity)
        if self.seat != self.canonical_game_identity["seat"]:
            raise ValueError("independent record seat must match canonical game identity seat")
        if type(self.record_hash) is not str or len(self.record_hash) != 64 or any(char not in _HEX64_V2 for char in self.record_hash):
            raise ValueError("independent record identity/record hash is invalid")
        validate_evidence_attestation_v2({
            "engine_seed_supported": self.engine_seed_supported, "replay_verified": self.replay_verified,
            "run_attestation": self.run_attestation, "seed_attestation": self.seed_attestation,
            "evidence_kind": "measured",
        })
        if self.outcome == "fault":
            _validate_fault_provenance_v2(self.fault_provenance)


@dataclass(frozen=True, slots=True)
class IndependentEvaluationRecordV3:
    """One pre-registered independent-arm readiness attempt.

    This record deliberately has no paired ledger fields: CABT's current
    engine cannot attest deterministic replay, so readiness inference must
    remain an independent-arm calculation.
    """

    lane_id: str
    training_seed: int
    policy_role: Literal["theta0", "candidate"]
    policy_artifact_sha256: str
    theta0_sha256: str
    repetition: int
    outcome: Literal["win", "loss", "draw", "fault"]
    seat: int
    opponent_family: str
    canonical_game_identity: Mapping[str, object]
    record_hash: str
    engine_seed_supported: bool
    replay_verified: bool
    run_attestation: str
    seed_attestation: str
    evidence_kind: str
    fault_provenance: Mapping[str, object] | None

    def __post_init__(self) -> None:
        if type(self.lane_id) is not str or not self.lane_id:
            raise ValueError("readiness record lane_id is invalid")
        if type(self.training_seed) is not int or self.training_seed < 0:
            raise ValueError("readiness record training_seed is invalid")
        if self.policy_role not in _POLICY_ROLES_V3:
            raise ValueError("readiness record policy_role is invalid")
        for field, value in (
            ("policy_artifact_sha256", self.policy_artifact_sha256),
            ("theta0_sha256", self.theta0_sha256),
            ("record_hash", self.record_hash),
        ):
            if type(value) is not str or len(value) != 64 or any(char not in _HEX64_V2 for char in value):
                raise ValueError(f"readiness record {field} is invalid")
        if type(self.repetition) is not int or self.repetition not in _READINESS_REPETITIONS_V3:
            raise ValueError("readiness record repetition is invalid")
        if self.outcome not in _OUTCOMES_V2:
            raise ValueError("readiness record outcome is invalid")
        if type(self.seat) is not int or self.seat not in _READINESS_SEATS_V3:
            raise ValueError("readiness record seat/opponent_family is invalid")
        if type(self.opponent_family) is not str or not self.opponent_family:
            raise ValueError("readiness record seat/opponent_family is invalid")
        _validate_identity_v2(self.canonical_game_identity)
        if self.seat != self.canonical_game_identity["seat"]:
            raise ValueError("readiness record seat must match canonical game identity seat")
        validate_evidence_attestation_v2({
            "engine_seed_supported": self.engine_seed_supported,
            "replay_verified": self.replay_verified,
            "run_attestation": self.run_attestation,
            "seed_attestation": self.seed_attestation,
            "evidence_kind": self.evidence_kind,
        })
        if self.outcome == "fault":
            _validate_fault_provenance_v2(self.fault_provenance)


def _readiness_arm_summary_v3(records: list[IndependentEvaluationRecordV3]) -> dict[str, object]:
    counts = Counter(record.outcome for record in records)
    denominator = len(records)
    wins = counts["win"]
    return {
        "denominator": denominator,
        "counts": {outcome: counts[outcome] for outcome in ("win", "loss", "draw", "fault")},
        "win_rate": wins / denominator,
        "loss_equivalent_rate": (denominator - wins) / denominator,
    }


def independent_readiness_summary_v3(
    records: list[IndependentEvaluationRecordV3],
    *,
    bootstrap_seed: int = 20260809,
    bootstrap_replicates: int = 20000,
) -> dict[str, object]:
    """Fail-closed, fixed-strata independent readiness inference.

    Every lane/seed/policy ledger has exactly six held-out opponents, two
    seats, and eight repetitions.  Faults remain attempted records and are
    counted as non-wins.  Missing attempts are never imputed.
    """
    if not records:
        raise ValueError("readiness records must be nonempty")
    if type(bootstrap_seed) is not int or bootstrap_seed < 0:
        raise ValueError("bootstrap_seed is invalid")
    if type(bootstrap_replicates) is not int or bootstrap_replicates <= 0:
        raise ValueError("bootstrap_replicates is invalid")

    grouped: dict[tuple[str, int], dict[str, list[IndependentEvaluationRecordV3]]] = {}
    seen: set[tuple[str, int, str, str, int, int]] = set()
    for record in records:
        if type(record) is not IndependentEvaluationRecordV3:
            raise ValueError("readiness records must be IndependentEvaluationRecordV3")
        validate_evidence_attestation_v2({
            "engine_seed_supported": record.engine_seed_supported,
            "replay_verified": record.replay_verified,
            "run_attestation": record.run_attestation,
            "seed_attestation": record.seed_attestation,
            "evidence_kind": record.evidence_kind,
        })
        opponent_id = record.canonical_game_identity["opponent_id"]
        key = (record.lane_id, record.training_seed, record.policy_role, opponent_id, record.seat, record.repetition)
        if key in seen:
            raise ValueError(f"duplicate readiness attempt: {key}")
        seen.add(key)
        grouped.setdefault((record.lane_id, record.training_seed), {"theta0": [], "candidate": []})[record.policy_role].append(record)

    generator = torch.Generator().manual_seed(bootstrap_seed)
    cell_bootstraps: list[torch.Tensor] = []
    cell_deltas: dict[str, float] = {}
    cells: dict[str, object] = {}
    for cell, arms in sorted(grouped.items()):
        lane_id, training_seed = cell
        theta0, candidate = arms["theta0"], arms["candidate"]
        if not theta0 or not candidate:
            raise ValueError(f"readiness cell must contain both policy arms: {cell}")
        theta0_hashes = {record.policy_artifact_sha256 for record in theta0}
        candidate_hashes = {record.policy_artifact_sha256 for record in candidate}
        if len(theta0_hashes) != 1 or len(candidate_hashes) != 1:
            raise ValueError(f"readiness cell policy artifact hash is not fixed: {cell}")
        theta0_hash = next(iter(theta0_hashes))
        if any(record.theta0_sha256 != theta0_hash for record in theta0 + candidate):
            raise ValueError(f"candidate theta0 hash does not match theta0 arm: {cell}")

        ledgers: dict[str, dict[tuple[str, int, int], IndependentEvaluationRecordV3]] = {}
        for role, arm_records in arms.items():
            ledger = {
                (record.canonical_game_identity["opponent_id"], record.seat, record.repetition): record
                for record in arm_records
            }
            opponent_ids = {key[0] for key in ledger}
            if len(opponent_ids) != _READINESS_OPPONENTS_V3 or len(ledger) != _READINESS_OPPONENTS_V3 * len(_READINESS_SEATS_V3) * len(_READINESS_REPETITIONS_V3):
                raise ValueError(f"readiness cell attempt ledger is not complete: {cell}|{role}")
            expected = {
                (opponent_id, seat, repetition)
                for opponent_id in opponent_ids
                for seat in _READINESS_SEATS_V3
                for repetition in _READINESS_REPETITIONS_V3
            }
            if set(ledger) != expected:
                raise ValueError(f"readiness cell attempt ledger is not complete: {cell}|{role}")
            ledgers[role] = ledger
        if set(ledgers["theta0"]) != set(ledgers["candidate"]):
            raise ValueError(f"readiness cell fixed strata do not match: {cell}")

        stratum_bootstraps: list[torch.Tensor] = []
        stratum_deltas: dict[str, float] = {}
        for opponent_id, seat, _ in sorted(ledgers["theta0"]):
            # Each repetition block appears once in the sorted key iteration;
            # construct its eight independent attempts from the fixed ledger.
            if _ != 0:
                continue
            theta0_values = torch.tensor([
                float(ledgers["theta0"][(opponent_id, seat, repetition)].outcome == "win")
                for repetition in _READINESS_REPETITIONS_V3
            ])
            candidate_values = torch.tensor([
                float(ledgers["candidate"][(opponent_id, seat, repetition)].outcome == "win")
                for repetition in _READINESS_REPETITIONS_V3
            ])
            label = f"opponent={opponent_id}|seat={seat}"
            delta = float(candidate_values.mean().item() - theta0_values.mean().item())
            stratum_deltas[label] = delta
            theta0_indices = torch.randint(0, len(theta0_values), (bootstrap_replicates, len(theta0_values)), generator=generator)
            candidate_indices = torch.randint(0, len(candidate_values), (bootstrap_replicates, len(candidate_values)), generator=generator)
            stratum_bootstraps.append(candidate_values[candidate_indices].mean(-1) - theta0_values[theta0_indices].mean(-1))
        cell_delta = sum(stratum_deltas.values()) / len(stratum_deltas)
        cell_key = f"lane={lane_id}|training_seed={training_seed}"
        cell_deltas[cell_key] = cell_delta
        cells[cell_key] = {
            "lane_id": lane_id,
            "training_seed": training_seed,
            "theta0": _readiness_arm_summary_v3(theta0),
            "candidate": _readiness_arm_summary_v3(candidate),
            "stratum_deltas": stratum_deltas,
            "delta": cell_delta,
        }
        cell_bootstraps.append(torch.stack(stratum_bootstraps).mean(0))

    bootstrap = torch.stack(cell_bootstraps).mean(0)
    macro_delta = sum(cell_deltas.values()) / len(cell_deltas)
    return {
        "method": "independent_readiness_fixed_strata_v3",
        "records": len(records),
        "cells": cells,
        "cell_deltas": cell_deltas,
        "macro_delta": macro_delta,
        "one_sided_95_lower": float(torch.quantile(bootstrap, 0.05).item()),
        "bootstrap_seed": bootstrap_seed,
        "bootstrap_replicates": bootstrap_replicates,
    }


def evaluation_inference_allowed_v2(*, engine_seed_supported: bool, replay_verified: bool) -> str:
    """Fail closed: paired/promotion inference requires an attested replayable engine."""
    if type(engine_seed_supported) is not bool or type(replay_verified) is not bool:
        raise ValueError("engine/replay capability must be JSON booleans")
    if engine_seed_supported is True and replay_verified is True:
        return "paired"
    raise ValueError("paired/promotion inference is unavailable; use independent stratified evaluation")


@dataclass(frozen=True, slots=True)
class PairedEvaluationRecordV2:
    """One candidate/baseline comparison on an identical evaluation ledger."""

    candidate_outcome: Literal["win", "loss", "draw", "fault"]
    baseline_outcome: Literal["win", "loss", "draw", "fault"]
    candidate_ledger_identity: str
    baseline_ledger_identity: str
    candidate_record_hash: str
    baseline_record_hash: str
    candidate_state_hash_sequence: tuple[str, ...]
    baseline_state_hash_sequence: tuple[str, ...]
    candidate_action_sequence: tuple[tuple[int, ...], ...]
    baseline_action_sequence: tuple[tuple[int, ...], ...]
    seat: int
    opponent_family: str

    def __post_init__(self) -> None:
        if self.candidate_outcome not in _OUTCOMES_V2 or self.baseline_outcome not in _OUTCOMES_V2:
            raise ValueError("outcomes must be win, loss, draw, or fault")
        if self.candidate_ledger_identity != self.baseline_ledger_identity:
            raise ValueError("paired record ledger identity does not match")
        if type(self.seat) is not int or self.seat not in (0, 1):
            raise ValueError("seat must be 0 or 1")
        if not self.opponent_family:
            raise ValueError("opponent_family must be nonempty")
        if not all(isinstance(value, str) and len(value) == 64 for value in (
            self.candidate_record_hash, self.baseline_record_hash,
        )):
            raise ValueError("record hashes must be SHA-256 strings")

    @property
    def complete(self) -> bool:
        return self.candidate_outcome != "fault" and self.baseline_outcome != "fault"


def wilson_interval_v2(successes: int, trials: int, *, z: float = 1.96) -> tuple[float, float]:
    if type(successes) is not int or type(trials) is not int or trials <= 0 or not 0 <= successes <= trials:
        raise ValueError("successes/trials are invalid")
    p = successes / trials
    denominator = 1 + z * z / trials
    center = (p + z * z / (2 * trials)) / denominator
    radius = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denominator
    return max(0.0, center - radius), min(1.0, center + radius)


def paired_summary_v2(
    candidate_outcomes: list[int], baseline_outcomes: list[int], *, seed: int = 0, bootstrap_samples: int = 2000,
) -> dict[str, float | int]:
    if len(candidate_outcomes) != len(baseline_outcomes) or not candidate_outcomes:
        raise ValueError("paired outcomes must be nonempty and aligned")
    if any(value not in (0, 1) for value in (*candidate_outcomes, *baseline_outcomes)):
        raise ValueError("outcomes must be binary win indicators")
    candidate_wins = sum(value > other for value, other in zip(candidate_outcomes, baseline_outcomes))
    baseline_wins = sum(other > value for value, other in zip(candidate_outcomes, baseline_outcomes))
    differences = torch.tensor([float(value - other) for value, other in zip(candidate_outcomes, baseline_outcomes)])
    paired_delta = float(differences.mean().item())
    generator = torch.Generator().manual_seed(seed)
    indices = torch.randint(0, len(differences), (bootstrap_samples, len(differences)), generator=generator)
    bootstrap = differences[indices].mean(-1)
    return {
        "games": len(differences), "candidate_wins": candidate_wins, "baseline_wins": baseline_wins,
        "paired_delta": paired_delta,
        "bootstrap_ci_low": float(torch.quantile(bootstrap, 0.025).item()),
        "bootstrap_ci_high": float(torch.quantile(bootstrap, 0.975).item()),
        "candidate_win_rate": float(sum(candidate_outcomes) / len(candidate_outcomes)),
        "baseline_win_rate": float(sum(baseline_outcomes) / len(baseline_outcomes)),
    }


def _outcome_counts_v2(records: list[PairedEvaluationRecordV2], side: str) -> dict[str, int]:
    counts = Counter(getattr(record, f"{side}_outcome") for record in records)
    return {
        "wins": counts["win"], "losses": counts["loss"],
        "draws": counts["draw"], "faults": counts["fault"],
    }


def _rate_intervals_v2(counts: dict[str, int], total: int) -> dict[str, tuple[float, float]]:
    if total <= 0:
        return {outcome: (0.0, 1.0) for outcome in _OUTCOMES_V2}
    return {
        "win": wilson_interval_v2(counts["wins"], total),
        "loss": wilson_interval_v2(counts["losses"], total),
        "draw": wilson_interval_v2(counts["draws"], total),
        "fault": wilson_interval_v2(counts["faults"], total),
    }


def paired_summary_from_records_v2(
    records: list[PairedEvaluationRecordV2], *, seed: int = 0, bootstrap_samples: int = 2000,
) -> dict[str, object]:
    """Summarize provenance-bearing pairs, bootstrapping only complete ones."""
    if not records:
        raise ValueError("paired records must be nonempty")
    # Dataclass validation catches usual callers; keep this boundary fail-closed
    # for deserialized or otherwise malformed record-like objects too.
    for record in records:
        if record.candidate_ledger_identity != record.baseline_ledger_identity:
            raise ValueError("paired record ledger identity does not match")
    complete = [record for record in records if record.complete]
    if not complete:
        raise ValueError("no complete valid pairs are available for bootstrap")
    candidate_binary = [1 if record.candidate_outcome == "win" else 0 for record in complete]
    baseline_binary = [1 if record.baseline_outcome == "win" else 0 for record in complete]
    summary: dict[str, object] = dict(
        paired_summary_v2(candidate_binary, baseline_binary, seed=seed, bootstrap_samples=bootstrap_samples)
    )
    summary["records"] = len(records)
    summary["complete_pairs"] = len(complete)
    candidate_counts = _outcome_counts_v2(records, "candidate")
    baseline_counts = _outcome_counts_v2(records, "baseline")
    summary["candidate"] = candidate_counts
    summary["baseline"] = baseline_counts
    summary["candidate_rate_intervals"] = _rate_intervals_v2(candidate_counts, len(records))
    summary["baseline_rate_intervals"] = _rate_intervals_v2(baseline_counts, len(records))
    by_seat: dict[str, object] = {}
    by_family: dict[str, object] = {}
    for key, subset, target in (
        ("0", [record for record in records if record.seat == 0], by_seat),
        ("1", [record for record in records if record.seat == 1], by_seat),
    ):
        target[key] = {"candidate": _outcome_counts_v2(subset, "candidate"), "baseline": _outcome_counts_v2(subset, "baseline")}
    for family in sorted({record.opponent_family for record in records}):
        subset = [record for record in records if record.opponent_family == family]
        by_family[family] = {"candidate": _outcome_counts_v2(subset, "candidate"), "baseline": _outcome_counts_v2(subset, "baseline")}
    summary["by_seat"] = by_seat
    summary["by_opponent_family"] = by_family
    return summary


def _arm_summary_v2(records: list[IndependentEvaluationRecordV2]) -> dict[str, object]:
    denominator = len(records)
    counts = Counter(record.outcome for record in records)
    named = {"win": counts["win"], "loss": counts["loss"], "draw": counts["draw"], "fault": counts["fault"]}
    return {
        "denominator": denominator,
        "counts": named,
        "rates": {name: count / denominator for name, count in named.items()},
        "intervals": {name: wilson_interval_v2(count, denominator) for name, count in named.items()},
    }


def independent_stratified_summary_v2(
    records: list[IndependentEvaluationRecordV2], *, seed: int = 0, bootstrap_samples: int = 2000,
) -> dict[str, object]:
    """Fixed-weight independent-arm comparison with arm-within-stratum bootstrap."""
    if not records:
        raise ValueError("independent evaluation records must be nonempty")
    if any(record.run_attestation.lower() in {"synthetic", "unit-only"} or record.seed_attestation.lower() in {"synthetic", "unit-only"} for record in records):
        raise ValueError("synthetic/unattested records cannot produce performance inference")
    grouped: dict[tuple[int, str], dict[str, list[IndependentEvaluationRecordV2]]] = {}
    for record in records:
        grouped.setdefault((record.seat, record.opponent_family), {"candidate": [], "baseline": []})[record.arm].append(record)
    missing = [key for key, arms in grouped.items() if not arms["candidate"] or not arms["baseline"]]
    if missing:
        raise ValueError(f"every stratum requires both arms, missing {missing}")
    strata: dict[str, object] = {}
    differences: list[float] = []
    generator = torch.Generator().manual_seed(seed)
    bootstrap_rows: list[torch.Tensor] = []
    for (seat, family), arms in sorted(grouped.items()):
        candidate = _arm_summary_v2(arms["candidate"])
        baseline = _arm_summary_v2(arms["baseline"])
        difference = float(candidate["rates"]["win"] - baseline["rates"]["win"])
        key = f"seat={seat}|family={family}"
        strata[key] = {"seat": seat, "opponent_family": family, "candidate": candidate, "baseline": baseline, "win_rate_difference": difference}
        differences.append(difference)
        c = torch.tensor([record.outcome == "win" for record in arms["candidate"]], dtype=torch.float32)
        b = torch.tensor([record.outcome == "win" for record in arms["baseline"]], dtype=torch.float32)
        c_index = torch.randint(0, len(c), (bootstrap_samples, len(c)), generator=generator)
        b_index = torch.randint(0, len(b), (bootstrap_samples, len(b)), generator=generator)
        bootstrap_rows.append(c[c_index].mean(-1) - b[b_index].mean(-1))
    bootstrap = torch.stack(bootstrap_rows).mean(0)
    return {
        "method": "independent_stratified_fixed_weight",
        "records": len(records),
        "strata": strata,
        "fixed_weight_difference": sum(differences) / len(differences),
        "bootstrap_ci_low": float(torch.quantile(bootstrap, 0.025).item()),
        "bootstrap_ci_high": float(torch.quantile(bootstrap, 0.975).item()),
        "engine_seed_supported": all(record.engine_seed_supported for record in records),
        "replay_verified": all(record.replay_verified for record in records),
    }


__all__ = [
    "IndependentEvaluationRecordV2", "IndependentEvaluationRecordV3", "PairedEvaluationRecordV2", "evaluation_inference_allowed_v2",
    "independent_readiness_summary_v3",
    "independent_stratified_summary_v2", "paired_summary_from_records_v2", "paired_summary_v2",
    "validate_evidence_attestation_v2", "wilson_interval_v2",
]
