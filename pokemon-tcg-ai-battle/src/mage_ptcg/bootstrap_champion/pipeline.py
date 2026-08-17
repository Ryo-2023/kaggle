"""File-oriented, resumable Bootstrap Champion stages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from mage_ptcg.continuous_league.contracts import atomic_write_json, content_id, load_json

from .candidates import build_joint_candidates, candidate_registry_id
from .contracts import (
    BootstrapChampionManifest,
    BootstrapContractError,
    DeckAsset,
    DeckCompatibility,
    InitializationMode,
    JointCandidate,
    PolicyAsset,
    write_manifest,
)
from .intake import BootstrapAssetRegistry, registry_from_catalog
from .tournament import BootstrapScore, build_candidate_schedule, rank_candidates, summarize_candidate


def _candidate_from_dict(payload: Mapping[str, Any]) -> JointCandidate:
    deck = DeckAsset(**dict(payload["deck"]))
    policy = dict(payload["policy"])
    policy["compatibility"] = DeckCompatibility(policy["compatibility"])
    candidate = JointCandidate(deck, PolicyAsset(**policy), str(payload["simulator_contract_hash"]))
    if payload.get("candidate_id") != candidate.candidate_id:
        raise BootstrapContractError("candidate registry identity mismatch")
    return candidate


def build_candidates_artifact(*, catalog: Any, deck_asset_registry: Path | None, simulator_contract_hash: str, output: Path) -> dict[str, Any]:
    assets = registry_from_catalog(catalog, deck_asset_registry=deck_asset_registry)
    candidates = build_joint_candidates(assets, simulator_contract_hash=simulator_contract_hash)
    document = {
        "schema_version": "bootstrap-candidate-registry-v1",
        "asset_registry": assets.to_dict(),
        "candidates": [candidate.to_dict() for candidate in candidates],
        "candidate_registry_id": candidate_registry_id(assets, candidates),
    }
    write_manifest(Path(output), document)
    return document


def load_candidates_artifact(path: Path) -> tuple[str, tuple[JointCandidate, ...]]:
    payload = load_json(path)
    if not isinstance(payload, Mapping) or payload.get("schema_version") != "bootstrap-candidate-registry-v1":
        raise BootstrapContractError("unsupported Bootstrap candidate registry")
    candidates = tuple(_candidate_from_dict(item) for item in payload.get("candidates", []))
    if not candidates:
        raise BootstrapContractError("Bootstrap candidate registry is empty")
    candidate_ids = [candidate.candidate_id for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise BootstrapContractError("Bootstrap candidate registry contains duplicates")
    registry_id = str(payload.get("candidate_registry_id", ""))
    if len(registry_id) != 64:
        raise BootstrapContractError("Bootstrap candidate registry ID is invalid")
    return registry_id, candidates


def write_schedule(
    *, candidate_registry_path: Path, opponent_instance_ids: Sequence[str], games_per_candidate: int, seed_namespace: str, output: Path
) -> dict[str, Any]:
    registry_id, candidates = load_candidates_artifact(candidate_registry_path)
    schedule = build_candidate_schedule(
        candidate_ids=[candidate.candidate_id for candidate in candidates],
        opponent_instance_ids=opponent_instance_ids,
        games_per_candidate=games_per_candidate,
        seed_namespace=seed_namespace,
    )
    document = {
        "schema_version": "bootstrap-tournament-schedule-v1",
        "candidate_registry_id": registry_id,
        "seed_namespace": seed_namespace,
        "games_per_candidate": games_per_candidate,
        "schedule_id": content_id("bootstrap-tournament-schedule-v1", [match.to_dict() for match in schedule]),
        "matches": [match.to_dict() for match in schedule],
    }
    write_manifest(Path(output), document)
    return document


def _read_results(path: Path, schedule: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    expected = {str(match["game_key"]): dict(match) for match in schedule["matches"]}
    rows_by_candidate: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BootstrapContractError(f"invalid Bootstrap result {path}:{line_number}") from exc
            key = str(row.get("game_key", ""))
            if key not in expected or key in seen:
                raise BootstrapContractError("Bootstrap results are outside schedule or duplicate")
            seen.add(key)
            merged = {**expected[key], **row}
            rows_by_candidate.setdefault(str(merged["candidate_id"]), []).append(merged)
    if set(expected) != seen:
        raise BootstrapContractError("Bootstrap results do not complete the fixed schedule")
    return rows_by_candidate


def select_champion(
    *, candidate_registry_path: Path, validation_schedule_path: Path, results_path: Path, screen_benchmark_id: str, output: Path
) -> dict[str, Any]:
    registry_id, candidates = load_candidates_artifact(candidate_registry_path)
    schedule = load_json(validation_schedule_path)
    if not isinstance(schedule, Mapping) or schedule.get("schema_version") != "bootstrap-tournament-schedule-v1":
        raise BootstrapContractError("unsupported Bootstrap validation schedule")
    if schedule.get("candidate_registry_id") != registry_id:
        raise BootstrapContractError("Bootstrap validation schedule uses another candidate registry")
    rows = _read_results(results_path, schedule)
    scores = [summarize_candidate(rows[candidate.candidate_id]) for candidate in candidates]
    ranked = rank_candidates(scores)
    if not ranked or ranked[0].fault_count:
        raise BootstrapContractError("no fault-free Bootstrap Champion candidate")
    selected = next(candidate for candidate in candidates if candidate.candidate_id == ranked[0].candidate_id)
    mode = (
        InitializationMode.DIRECT_CHECKPOINT
        if selected.policy.policy_kind == "runtime_policy"
        else InitializationMode.TEACHER_DISTILLATION
    )
    manifest = BootstrapChampionManifest.build(
        candidate_registry_id=registry_id,
        screen_benchmark_id=screen_benchmark_id,
        validation_benchmark_id=str(schedule["schedule_id"]),
        candidate=selected,
        initialization_mode=mode,
        score_summary=ranked[0].to_dict(),
    )
    document = {**manifest.to_dict(), "ranking": [score.to_dict() for score in ranked]}
    write_manifest(Path(output), document)
    return document


def select_finalists(
    *, candidate_registry_path: Path, schedule_path: Path, results_path: Path, finalists: int, output: Path
) -> dict[str, Any]:
    if finalists < 1:
        raise BootstrapContractError("Bootstrap finalist count must be positive")
    parent_registry_id, candidates = load_candidates_artifact(candidate_registry_path)
    schedule = load_json(schedule_path)
    if not isinstance(schedule, Mapping) or schedule.get("schema_version") != "bootstrap-tournament-schedule-v1":
        raise BootstrapContractError("unsupported Bootstrap screen schedule")
    if schedule.get("candidate_registry_id") != parent_registry_id:
        raise BootstrapContractError("Bootstrap screen schedule uses another candidate registry")
    rows = _read_results(results_path, schedule)
    scores = [summarize_candidate(rows[candidate.candidate_id]) for candidate in candidates]
    ranked = rank_candidates(scores)
    selected_ids = {score.candidate_id for score in ranked[:finalists] if score.fault_count == 0}
    selected = tuple(candidate for candidate in candidates if candidate.candidate_id in selected_ids)
    if not selected:
        raise BootstrapContractError("Bootstrap screen has no fault-free finalist")
    registry_id = content_id(
        "bootstrap-finalist-registry-v1",
        {"parent_candidate_registry_id": parent_registry_id, "candidate_ids": [candidate.candidate_id for candidate in selected], "screen_schedule_id": schedule["schedule_id"]},
    )
    document = {
        "schema_version": "bootstrap-candidate-registry-v1",
        "candidate_registry_id": registry_id,
        "parent_candidate_registry_id": parent_registry_id,
        "screen_schedule_id": schedule["schedule_id"],
        "candidates": [candidate.to_dict() for candidate in selected],
        "screen_ranking": [score.to_dict() for score in ranked],
    }
    write_manifest(Path(output), document)
    return document
