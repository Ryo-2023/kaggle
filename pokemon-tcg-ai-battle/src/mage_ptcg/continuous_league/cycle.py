"""次の有限学習周期で不足する対戦経験を決定する。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .catalog import CatalogSnapshot
from .contracts import LeagueContractError, content_id
from .coverage import ReplayCoverage


@dataclass(frozen=True, slots=True)
class CyclePlan:
    catalog_snapshot_id: str
    replay_dataset_version_id: str
    population_epoch_id: str
    selected_opponent_instance_ids: tuple[str, ...]
    missing_opponent_instance_ids: tuple[str, ...]
    opponent_episode_quotas: tuple[tuple[str, int], ...]
    cycle_plan_id: str

    @classmethod
    def build(
        cls,
        *,
        catalog: CatalogSnapshot,
        coverage: ReplayCoverage,
        roles: Iterable[str],
        bootstrap_episodes_per_new_opponent: int,
        refresh_episodes_per_known_opponent: int = 0,
    ) -> "CyclePlan":
        if bootstrap_episodes_per_new_opponent < 2 or bootstrap_episodes_per_new_opponent % 2:
            raise LeagueContractError("new-opponent bootstrap episodes must be a positive even number")
        if refresh_episodes_per_known_opponent < 0 or refresh_episodes_per_known_opponent % 2:
            raise LeagueContractError("known-opponent refresh episodes must be zero or even")
        selected = catalog.by_role(*tuple(roles))
        if not selected:
            raise LeagueContractError("cycle plan selection contains no enabled opponents")
        observed = coverage.opponent_pairs
        missing = tuple(
            entry.opponent_instance_id
            for entry in selected
            if (entry.policy_hash, entry.deck_hash) not in observed
        )
        quotas = tuple(
            (entry.opponent_instance_id,
             bootstrap_episodes_per_new_opponent
             if entry.opponent_instance_id in missing
             else refresh_episodes_per_known_opponent)
            for entry in selected
            if (
                entry.opponent_instance_id in missing
                or refresh_episodes_per_known_opponent
            )
        )
        identity = {
            "catalog_snapshot_id": catalog.catalog_snapshot_id,
            "replay_dataset_version_id": coverage.replay_dataset_version_id,
            "population_epoch_id": coverage.population_epoch_id,
            "selected_opponent_instance_ids": [entry.opponent_instance_id for entry in selected],
            "missing_opponent_instance_ids": list(missing),
            "opponent_episode_quotas": [list(item) for item in quotas],
        }
        return cls(
            catalog_snapshot_id=catalog.catalog_snapshot_id,
            replay_dataset_version_id=coverage.replay_dataset_version_id,
            population_epoch_id=coverage.population_epoch_id,
            selected_opponent_instance_ids=tuple(identity["selected_opponent_instance_ids"]),
            missing_opponent_instance_ids=missing,
            opponent_episode_quotas=quotas,
            cycle_plan_id=content_id("continuous-league-cycle-plan-v1", identity),
        )

    def to_dict(self) -> dict[str, Any]:
        document = asdict(self)
        document["opponent_episode_quotas"] = [
            {"opponent_instance_id": opponent_id, "episodes": episodes}
            for opponent_id, episodes in self.opponent_episode_quotas
        ]
        document["collection_required"] = bool(self.opponent_episode_quotas)
        return document


def plan_cycle_from_manifest(
    *,
    catalog: CatalogSnapshot,
    replay_manifest: Path,
    roles: Iterable[str],
    bootstrap_episodes_per_new_opponent: int,
    refresh_episodes_per_known_opponent: int = 0,
) -> CyclePlan:
    return CyclePlan.build(
        catalog=catalog,
        coverage=ReplayCoverage.from_replay_manifest(replay_manifest),
        roles=roles,
        bootstrap_episodes_per_new_opponent=bootstrap_episodes_per_new_opponent,
        refresh_episodes_per_known_opponent=refresh_episodes_per_known_opponent,
    )
