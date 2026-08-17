"""C5 League-lite plans and safe unavailable-capability behavior."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import product
from typing import Iterable

from mage_ptcg.distillation.contracts import atomic_write_json, digest


class LeagueCapabilityUnavailable(RuntimeError):
    """Actual cabt league execution is unavailable in this environment."""


@dataclass(frozen=True, slots=True)
class LeagueAgent:
    agent_id: str
    revision: str
    classification: str


@dataclass(frozen=True, slots=True)
class LeaguePlan:
    champion_id: str
    agents: tuple[LeagueAgent, ...]
    seeds: tuple[int, ...]
    deck_fingerprint: str
    config_hash: str
    timeout_ms: int
    environment_version: str

    def __post_init__(self) -> None:
        ids = [agent.agent_id for agent in self.agents]
        if not self.champion_id or self.champion_id not in ids or len(ids) != len(set(ids)):
            raise ValueError("champion must occur exactly once in unique agents")
        if not self.seeds or len(self.seeds) != len(set(self.seeds)) or self.timeout_ms <= 0:
            raise ValueError("seed schedule and timeout must be valid")
        if not self.deck_fingerprint or not self.config_hash or not self.environment_version:
            raise ValueError("deck/config/environment provenance is required")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def deterministic_pairings(plan: LeaguePlan) -> list[dict[str, object]]:
    challengers = [agent.agent_id for agent in plan.agents if agent.agent_id != plan.champion_id]
    return [
        {"match_id": digest({"champion": plan.champion_id, "challenger": challenger, "seed": seed, "side": side}, domain="league-match"), "seed": seed, "side": side, "player0": plan.champion_id if side == 0 else challenger, "player1": challenger if side == 0 else plan.champion_id}
        for challenger, seed, side in product(sorted(challengers), sorted(plan.seeds), (0, 1))
    ]


def initial_run_manifest(plan: LeaguePlan) -> dict[str, object]:
    pairings = deterministic_pairings(plan)
    return {"schema_version": "league-lite-run-v1", "plan": plan.to_dict(), "pairings": pairings, "completed": [], "run_hash": digest(pairings, domain="league-run")}


def run_actual_cabt(plan: LeaguePlan, *, output_path: str) -> None:
    """Fail closed until a documented public cabt runner exists."""
    manifest = initial_run_manifest(plan)
    manifest["status"] = "CAPABILITY_UNAVAILABLE"
    manifest["reason"] = "no documented privacy-safe cabt league runner"
    atomic_write_json(output_path, manifest)
    raise LeagueCapabilityUnavailable(manifest["reason"])


__all__ = ["LeagueAgent", "LeagueCapabilityUnavailable", "LeaguePlan", "deterministic_pairings", "initial_run_manifest", "run_actual_cabt"]
