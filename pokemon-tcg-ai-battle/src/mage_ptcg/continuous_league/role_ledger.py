"""lineage/deck leakage を防ぐ append-stable role ledger。"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .catalog import CatalogEntry
from .contracts import LeagueContractError, content_id


@dataclass(frozen=True, slots=True)
class RoleAssignment:
    component_id: str
    role: str
    asset_ids: tuple[str, ...]
    policy_ids: tuple[str, ...]
    deck_ids: tuple[str, ...]
    source_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "role": self.role,
            "asset_ids": list(self.asset_ids),
            "policy_ids": list(self.policy_ids),
            "deck_ids": list(self.deck_ids),
            "source_ids": list(self.source_ids),
        }


@dataclass(frozen=True, slots=True)
class RoleLedger:
    schema_version: int
    assignments: tuple[RoleAssignment, ...]
    role_ledger_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "role_ledger_id": self.role_ledger_id,
            "assignments": [assignment.to_dict() for assignment in self.assignments],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RoleLedger":
        assignments = tuple(
            RoleAssignment(
                component_id=str(item["component_id"]),
                role=str(item["role"]),
                asset_ids=tuple(item["asset_ids"]),
                policy_ids=tuple(item["policy_ids"]),
                deck_ids=tuple(item["deck_ids"]),
                source_ids=tuple(item["source_ids"]),
            )
            for item in payload["assignments"]
        )
        rebuilt = cls.build_from_assignments(assignments)
        if rebuilt.role_ledger_id != payload.get("role_ledger_id"):
            raise LeagueContractError("role ledger hash mismatch")
        return rebuilt

    @classmethod
    def build_from_assignments(
        cls, assignments: Iterable[RoleAssignment]
    ) -> "RoleLedger":
        ordered = tuple(sorted(assignments, key=lambda item: item.component_id))
        payload = {
            "schema_version": 1,
            "assignments": [assignment.to_dict() for assignment in ordered],
        }
        return cls(
            schema_version=1,
            assignments=ordered,
            role_ledger_id=content_id("role-ledger-v1", payload),
        )


def _components(entries: tuple[CatalogEntry, ...]) -> list[tuple[CatalogEntry, ...]]:
    parent = list(range(len(entries)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    identity_owner: dict[tuple[str, str], int] = {}
    for index, entry in enumerate(entries):
        identities = {
            ("asset", entry.asset_id),
            ("policy", entry.policy_id),
            ("deck", entry.deck_id),
            ("source", entry.source_id),
        }
        if entry.parent_policy_id:
            identities.add(("policy", entry.parent_policy_id))
        for identity in identities:
            if identity in identity_owner:
                union(index, identity_owner[identity])
            else:
                identity_owner[identity] = index

    groups: dict[int, list[CatalogEntry]] = {}
    for index, entry in enumerate(entries):
        groups.setdefault(find(index), []).append(entry)
    return [
        tuple(sorted(group, key=lambda item: item.asset_id))
        for group in groups.values()
    ]


def _assignment(group: tuple[CatalogEntry, ...], role: str) -> RoleAssignment:
    identity = {
        "asset_ids": sorted({entry.asset_id for entry in group}),
        "policy_ids": sorted({entry.policy_id for entry in group}),
        "deck_ids": sorted({entry.deck_id for entry in group}),
        "source_ids": sorted({entry.source_id for entry in group}),
    }
    return RoleAssignment(
        component_id=content_id("role-component-v1", identity),
        role=role,
        asset_ids=tuple(identity["asset_ids"]),
        policy_ids=tuple(identity["policy_ids"]),
        deck_ids=tuple(identity["deck_ids"]),
        source_ids=tuple(identity["source_ids"]),
    )


def extend_role_ledger(
    entries: Iterable[CatalogEntry],
    *,
    prior: RoleLedger | None = None,
    role_counts: Mapping[str, int] | None = None,
    seed: int = 71_000,
) -> RoleLedger:
    """新規 component のみ割当て、既存 component の role は変更しない。"""

    ordered_entries = tuple(sorted(entries, key=lambda entry: entry.asset_id))
    groups = _components(ordered_entries)
    prior_assignments = prior.assignments if prior else ()

    assigned: list[RoleAssignment] = []
    unassigned: list[tuple[CatalogEntry, ...]] = []
    for group in groups:
        group_asset_ids = {entry.asset_id for entry in group}
        group_policy_ids = {entry.policy_id for entry in group}
        group_deck_ids = {entry.deck_id for entry in group}
        group_source_ids = {entry.source_id for entry in group}
        matched_roles = {
            old.role
            for old in prior_assignments
            if group_asset_ids.intersection(old.asset_ids)
            or group_policy_ids.intersection(old.policy_ids)
            or group_deck_ids.intersection(old.deck_ids)
            or group_source_ids.intersection(old.source_ids)
        }
        if len(matched_roles) > 1:
            raise LeagueContractError(
                "new identity merges components assigned to different roles"
            )
        if matched_roles:
            assigned.append(_assignment(group, next(iter(matched_roles))))
        else:
            unassigned.append(group)

    requested = dict(role_counts or {"TRAINING_ACTIVE": len(unassigned)})
    if any(count < 0 for count in requested.values()):
        raise LeagueContractError("role counts must be non-negative")
    if sum(requested.values()) < len(unassigned):
        requested["TRAINING_ACTIVE"] = requested.get("TRAINING_ACTIVE", 0) + (
            len(unassigned) - sum(requested.values())
        )
    roles: list[str] = []
    for role, count in sorted(requested.items()):
        roles.extend([role] * count)
    if len(roles) > len(unassigned):
        roles = roles[: len(unassigned)]
    random.Random(seed).shuffle(unassigned)
    assigned.extend(
        _assignment(group, role) for group, role in zip(unassigned, roles, strict=True)
    )
    return RoleLedger.build_from_assignments(assigned)


def initialize_role_ledger(
    entries: Iterable[CatalogEntry], asset_roles: Mapping[str, str]
) -> RoleLedger:
    """既存 split を component 単位の初期 ledger へ移行する。"""

    ordered_entries = tuple(sorted(entries, key=lambda entry: entry.asset_id))
    unknown = set(asset_roles) - {entry.asset_id for entry in ordered_entries}
    if unknown:
        raise LeagueContractError(
            f"initial role map contains unknown assets: {sorted(unknown)}"
        )
    assignments = []
    for group in _components(ordered_entries):
        missing = [entry.asset_id for entry in group if entry.asset_id not in asset_roles]
        if missing:
            raise LeagueContractError(
                f"initial role map misses component assets: {missing}"
            )
        roles = {asset_roles[entry.asset_id] for entry in group}
        if len(roles) != 1:
            raise LeagueContractError(
                "initial role map splits a connected policy/deck/source component"
            )
        assignments.append(_assignment(group, next(iter(roles))))
    return RoleLedger.build_from_assignments(assignments)
