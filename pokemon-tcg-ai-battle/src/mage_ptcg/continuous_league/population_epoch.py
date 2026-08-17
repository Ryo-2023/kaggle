"""Population epoch と strict resume / rollover の分離。"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from mage_ptcg.policy_learning.r2d3.checkpoint import save_checkpoint

from .contracts import LeagueContractError, atomic_write_json, content_id, utc_now
from .replay_sealer import bootstrap_seat_coverage


@dataclass(frozen=True, slots=True)
class PopulationEpoch:
    population_epoch_id: str
    member_probabilities: tuple[tuple[str, float], ...]
    parent_population_epoch_id: str | None

    @classmethod
    def build(
        cls,
        member_probabilities: Mapping[str, float],
        *,
        parent_population_epoch_id: str | None = None,
    ) -> "PopulationEpoch":
        ordered = tuple(
            sorted((str(key), float(value)) for key, value in member_probabilities.items())
        )
        if not ordered or len({key for key, _ in ordered}) != len(ordered):
            raise LeagueContractError("population needs distinct members")
        total = sum(value for _, value in ordered)
        if any(value < 0 for _, value in ordered) or abs(total - 1.0) > 1e-8:
            raise LeagueContractError("population probabilities must sum to one")
        identity = {
            "member_probabilities": ordered,
            "parent_population_epoch_id": parent_population_epoch_id,
        }
        return cls(
            population_epoch_id=content_id("population-epoch-v1", identity),
            member_probabilities=ordered,
            parent_population_epoch_id=parent_population_epoch_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "population_epoch_id": self.population_epoch_id,
            "member_probabilities": [
                {"opponent_instance_id": member, "probability": probability}
                for member, probability in self.member_probabilities
            ],
            "parent_population_epoch_id": self.parent_population_epoch_id,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PopulationEpoch":
        rebuilt = cls.build(
            {
                str(item["opponent_instance_id"]): float(item["probability"])
                for item in payload["member_probabilities"]
            },
            parent_population_epoch_id=payload.get("parent_population_epoch_id"),
        )
        if rebuilt.population_epoch_id != payload.get("population_epoch_id"):
            raise LeagueContractError("population epoch hash mismatch")
        return rebuilt


def build_rollover_manifest(
    *,
    old_epoch: PopulationEpoch,
    new_epoch: PopulationEpoch,
    new_opponent_instance_ids: Iterable[str],
    bootstrap_chunk_manifests: Iterable[Path],
    global_step: int,
    replay_dataset_version_id: str,
    inherit_optimizer: bool,
) -> dict[str, Any]:
    if new_epoch.parent_population_epoch_id != old_epoch.population_epoch_id:
        raise LeagueContractError("new population epoch must bind its parent")
    new_ids = tuple(sorted(set(new_opponent_instance_ids)))
    coverage = bootstrap_seat_coverage(bootstrap_chunk_manifests, new_ids)
    incomplete = {
        opponent_id: seats for opponent_id, seats in coverage.items() if seats != [0, 1]
    }
    if incomplete:
        raise LeagueContractError(
            f"population rollover bootstrap lacks both seats: {incomplete}"
        )
    identity = {
        "old_population_epoch_id": old_epoch.population_epoch_id,
        "new_population_epoch_id": new_epoch.population_epoch_id,
        "new_opponent_instance_ids": list(new_ids),
        "bootstrap_seat_coverage": coverage,
        "global_step": int(global_step),
        "next_epoch_step": 0,
        "replay_dataset_version_id": replay_dataset_version_id,
        "model_transfer": "inherit",
        "target_transfer": "inherit",
        "optimizer_transfer": "inherit" if inherit_optimizer else "reset",
        "scheduler_transfer": "reset",
        "rng_transfer": "reset",
        "per_priority_transfer": "uniform-reset-then-td-update",
    }
    return {
        "schema_version": 1,
        "population_transition_id": content_id("population-transition-v1", identity),
        **identity,
        "created_at": utc_now(),
    }


def apply_population_rollover(
    *,
    source_checkpoint_path: Path,
    destination_checkpoint_path: Path,
    model: Any,
    target: Any,
    optimizer: Any,
    scheduler: Any | None,
    replay: Any,
    old_population_epoch_id: str,
    old_replay_dataset_version_id: str,
    new_population_epoch_id: str,
    new_replay_dataset_version_id: str,
    transition_manifest: Mapping[str, Any],
    inherit_optimizer: bool,
    seed: int,
) -> dict[str, Any]:
    import torch

    payload = torch.load(
        Path(source_checkpoint_path), map_location="cpu", weights_only=False
    )
    if (
        payload.get("population_hash") != old_population_epoch_id
        or payload.get("replay_manifest_hash") != old_replay_dataset_version_id
    ):
        raise LeagueContractError("rollover source checkpoint identity mismatch")
    if (
        transition_manifest.get("old_population_epoch_id") != old_population_epoch_id
        or transition_manifest.get("new_population_epoch_id")
        != new_population_epoch_id
        or transition_manifest.get("replay_dataset_version_id")
        != new_replay_dataset_version_id
    ):
        raise LeagueContractError("rollover transition manifest mismatch")
    model.load_state_dict(payload["model"], strict=True)
    target.load_state_dict(payload["target"], strict=True)
    if inherit_optimizer:
        optimizer.load_state_dict(payload["optimizer"])
    replay.reset_priorities()
    random.seed(seed)
    torch.manual_seed(seed)
    try:
        import numpy

        numpy.random.seed(seed)
    except ImportError:
        pass
    step = int(payload["step"])
    metadata = save_checkpoint(
        destination_checkpoint_path,
        model=model,
        target=target,
        optimizer=optimizer,
        scheduler=scheduler,
        replay=replay,
        population_hash=new_population_epoch_id,
        replay_manifest_hash=new_replay_dataset_version_id,
        training_identity_hash=transition_manifest["population_transition_id"],
        step=step,
        strict_state=True,
    )
    atomic_write_json(
        Path(destination_checkpoint_path).with_suffix(".transition.json"),
        dict(transition_manifest),
    )
    return metadata
