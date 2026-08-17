"""Immutable PSRO-mixture sampling and online trajectory provenance."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import random
from typing import Any, Mapping

from .replay import ReplaySample


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class MixtureMember:
    opponent_policy_id: str
    probability: float
    policy_hash: str
    source_lineage: str
    family: str
    kind: str

    def __post_init__(self) -> None:
        if not all((self.opponent_policy_id, self.policy_hash, self.source_lineage, self.family, self.kind)):
            raise ValueError("PSRO mixture member identity is incomplete")
        if not math.isfinite(self.probability) or self.probability < 0:
            raise ValueError("PSRO probability must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class MixtureManifest:
    members: tuple[MixtureMember, ...]
    mixture_hash: str

    @classmethod
    def build(cls, members: list[MixtureMember]) -> "MixtureManifest":
        if not members or len({item.opponent_policy_id for item in members}) != len(members):
            raise ValueError("PSRO mixture needs distinct members")
        total = sum(item.probability for item in members)
        if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-8):
            raise ValueError(f"PSRO probabilities sum to {total}, not one")
        ordered = tuple(sorted(members, key=lambda item: item.opponent_policy_id))
        return cls(ordered, _digest([asdict(item) for item in ordered]))

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> "MixtureManifest":
        population = value.get("population")
        strategy = value.get("meta_strategy")
        if not isinstance(population, list) or not isinstance(strategy, Mapping):
            raise ValueError("PSRO payload lacks population or meta-strategy")
        rows = []
        for item in population:
            if isinstance(item, str):
                item = {"id": item, "kind": item, "policy_hash": _digest(item),
                        "source_lineage": _digest(["lineage", item]), "family": item.upper()}
            identifier = str(item.get("id", ""))
            rows.append(MixtureMember(
                identifier, float(strategy.get(identifier, 0.0)), str(item.get("policy_hash", "")),
                str(item.get("source_lineage") or item.get("lineage") or ""),
                str(item.get("family", "")), str(item.get("kind", "")),
            ))
        return cls.build(rows)

    def sample(self, *, seed: int) -> MixtureMember:
        rng = random.Random(seed)
        return rng.choices(self.members, weights=[item.probability for item in self.members], k=1)[0]

    def document(self) -> dict[str, Any]:
        return {"schema": "r2d3-psro-opponent-mixture-v1", "mixture_hash": self.mixture_hash,
                "members": [asdict(item) for item in self.members]}


def collection_record(
    *, game_id: str, mixture: MixtureManifest, member: MixtureMember,
    candidate_policy_version: str, result: str, winner: int | None, candidate_side: int,
    sequence_count: int,
) -> dict[str, Any]:
    if member not in mixture.members:
        raise ValueError("sampled opponent is not in the frozen mixture")
    if not game_id or not candidate_policy_version or candidate_side not in (0, 1) or sequence_count < 0:
        raise ValueError("online collection record is invalid")
    return {
        "game_id": game_id, "meta_strategy_hash": mixture.mixture_hash,
        "sampled_opponent": member.opponent_policy_id, "sampling_probability": member.probability,
        "source_policy_hash": member.policy_hash, "source_lineage": member.source_lineage,
        "opponent_family": member.family, "opponent_kind": member.kind,
        "candidate_policy_version": candidate_policy_version, "result": result,
        "winner": winner, "candidate_side": candidate_side, "sequence_count": sequence_count,
    }


class AlternatingReplayPartitions:
    """Use both frozen offline and PSRO-online partitions without copying."""
    def __init__(self, offline: Any, online: Any) -> None:
        if not len(offline) or not len(online):
            raise ValueError("both replay partitions must be non-empty")
        self.offline, self.online = offline, online

    def __len__(self) -> int:
        return len(self.offline) + len(self.online)

    def sample(self, batch_size: int, *, beta: float, demonstration_ratio: float = 0.0,
               seed: int | None = None, episode_first: bool = False,
               source_balanced: bool = False) -> ReplaySample:
        use_online = bool((seed or 0) % 2)
        source = self.online if use_online else self.offline
        sample = source.sample(min(batch_size, len(source)), beta=beta,
                               demonstration_ratio=0.0 if use_online else demonstration_ratio,
                               seed=seed, episode_first=episode_first,
                               source_balanced=source_balanced)
        offset = len(self.offline) if use_online else 0
        return ReplaySample(sample.sequences, tuple(index + offset for index in sample.indices),
                            sample.weights, sample.demonstrations)

    def update_priorities(self, indices: Any, priorities: Any, *, importance: Any | None = None) -> list[dict[str, object]]:
        index_values = list(indices); priority_values = list(priorities)
        importance_values = list(importance) if importance is not None else [None] * len(index_values)
        offline_rows = []; online_rows = []
        for index, priority, weight in zip(index_values, priority_values, importance_values, strict=True):
            target = online_rows if index >= len(self.offline) else offline_rows
            target.append((index - len(self.offline) if target is online_rows else index, priority, weight))
        updates: list[dict[str, object]] = []
        for source, rows, offset in ((self.offline, offline_rows, 0), (self.online, online_rows, len(self.offline))):
            if not rows:
                continue
            current = source.update_priorities([row[0] for row in rows], [row[1] for row in rows],
                                               importance=[row[2] for row in rows])
            for item in current:
                item["sample_id"] = int(item["sample_id"]) + offset
                item["partition"] = "online" if offset else "offline"
            updates.extend(current)
        return updates

    def priority_state(self) -> dict[str, object]:
        return {
            "schema": "r2d3-alternating-priority-state-v1",
            "offline": self.offline.priority_state(),
            "online": self.online.priority_state(),
        }

    def load_priority_state(self, state: object) -> None:
        if not isinstance(state, dict) or state.get("schema") != "r2d3-alternating-priority-state-v1":
            raise ValueError("unsupported alternating replay priority state")
        self.offline.load_priority_state(state.get("offline"))
        self.online.load_priority_state(state.get("online"))

    def reset_priorities(self, value: float | None = None) -> None:
        self.offline.reset_priorities(value)
        self.online.reset_priorities(value)


__all__ = ["AlternatingReplayPartitions", "MixtureManifest", "MixtureMember", "collection_record"]
