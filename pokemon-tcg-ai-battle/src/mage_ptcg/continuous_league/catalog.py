"""Opponent catalog と deck-policy-instance 階層。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Iterable, Mapping

from .contracts import LeagueContractError, content_id, require_sha256


_ROLES = {
    "TRAINING_ACTIVE",
    "TRAINING_RESERVE",
    "BENCHMARK_VISIBLE",
    "BENCHMARK_SEALED",
    "CALIBRATION_ONLY",
}


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    asset_id: str
    policy_id: str
    deck_id: str
    source_id: str
    policy_kind: str
    runtime_path: str
    deck_path: str
    policy_hash: str
    deck_hash: str
    source_hash: str
    role: str
    deck_family: str = "unknown"
    archetype_id: str = "unknown"
    parent_policy_id: str | None = None
    runtime_config_hash: str = "0" * 64
    enabled: bool = True

    def __post_init__(self) -> None:
        for field_name in (
            "asset_id",
            "policy_id",
            "deck_id",
            "source_id",
            "policy_kind",
            "runtime_path",
            "deck_path",
        ):
            if not getattr(self, field_name):
                raise LeagueContractError(f"{field_name} must be non-empty")
        if self.role not in _ROLES:
            raise LeagueContractError(f"unknown catalog role: {self.role}")
        for field_name in (
            "policy_hash",
            "deck_hash",
            "source_hash",
            "runtime_config_hash",
        ):
            require_sha256(getattr(self, field_name), field_name)

    @property
    def opponent_instance_id(self) -> str:
        return content_id(
            "opponent-instance-v1",
            {
                "deck_id": self.deck_id,
                "deck_hash": self.deck_hash,
                "policy_id": self.policy_id,
                "policy_hash": self.policy_hash,
                "runtime_config_hash": self.runtime_config_hash,
            },
        )

    @property
    def effective_archetype_id(self) -> str:
        if self.archetype_id != "unknown":
            return self.archetype_id
        return self.deck_family

    def to_dict(self) -> dict[str, Any]:
        document = asdict(self)
        document["opponent_instance_id"] = self.opponent_instance_id
        return document

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CatalogEntry":
        fields = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: payload[key] for key in fields if key in payload})

    @classmethod
    def from_submitted_asset(
        cls,
        asset: Any,
        *,
        role: str,
        runtime_config_hash: str | None = None,
    ) -> "CatalogEntry":
        if hasattr(asset, "to_dict"):
            asset_document = asset.to_dict()
        elif is_dataclass(asset):
            asset_document = asdict(asset)
        else:
            asset_document = dict(asset)
        policy_hash = str(
            asset_document.get("policy_hash")
            or asset_document.get("source_sha256")
            or asset_document["asset_id"]
        )
        deck_hash = str(
            asset_document.get("deck_hash")
            or asset_document.get("deck_sha256")
            or asset_document["deck_id"]
        )
        source_hash = str(
            asset_document.get("source_hash")
            or asset_document.get("source_sha256")
            or policy_hash
        )
        return cls(
            asset_id=str(asset_document["asset_id"]),
            policy_id=str(
                asset_document.get("policy_hash")
                or asset_document.get("policy_id")
                or asset_document.get("policy_lineage_id")
                or asset_document["asset_id"]
            ),
            deck_id=str(
                asset_document.get("deck_hash") or asset_document["deck_id"]
            ),
            source_id=str(
                asset_document.get("source_lineage")
                or asset_document.get("source_id")
                or asset_document.get("source_ref")
                or asset_document["asset_id"]
            ),
            policy_kind=str(asset_document.get("agent_kind", "submitted_snapshot")),
            runtime_path=str(
                asset_document.get("runtime_path")
                or asset_document.get("source_path")
                or asset_document.get("agent_path")
                or f"git:{asset_document.get('source_commit', 'unknown')}"
            ),
            deck_path=str(
                asset_document.get("deck_path") or asset_document["deck_id"]
            ),
            policy_hash=require_sha256(policy_hash, "policy_hash"),
            deck_hash=require_sha256(deck_hash, "deck_hash"),
            source_hash=(
                source_hash
                if len(source_hash) == 64
                else content_id("catalog-source-v1", source_hash)
            ),
            role=role,
            deck_family=str(asset_document.get("deck_family", "unknown")),
            archetype_id=str(asset_document.get("archetype_id", "unknown")),
            parent_policy_id=asset_document.get("parent_policy_id"),
            runtime_config_hash=(
                runtime_config_hash
                or (
                    str(asset_document.get("runtime_config_hash"))
                    if len(str(asset_document.get("runtime_config_hash", ""))) == 64
                    else content_id(
                        "runtime-config-v1",
                        {
                            "entrypoint": asset_document.get("entrypoint"),
                            "adapter_hash": asset_document.get("adapter_hash"),
                        },
                    )
                )
            ),
            enabled=bool(asset_document.get("enabled", True)),
        )


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    schema_version: int
    entries: tuple[CatalogEntry, ...]
    catalog_snapshot_id: str

    @classmethod
    def build(cls, entries: Iterable[CatalogEntry]) -> "CatalogSnapshot":
        ordered = tuple(sorted(entries, key=lambda entry: entry.asset_id))
        if not ordered:
            raise LeagueContractError("catalog snapshot must contain at least one entry")
        asset_ids = [entry.asset_id for entry in ordered]
        if len(asset_ids) != len(set(asset_ids)):
            raise LeagueContractError("catalog asset_id values must be unique")
        instance_ids = [entry.opponent_instance_id for entry in ordered if entry.enabled]
        if len(instance_ids) != len(set(instance_ids)):
            raise LeagueContractError(
                "enabled catalog entries must have unique opponent_instance_id"
            )
        payload = {
            "schema_version": 1,
            "entries": [entry.to_dict() for entry in ordered],
        }
        return cls(
            schema_version=1,
            entries=ordered,
            catalog_snapshot_id=content_id("catalog-snapshot-v1", payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "catalog_snapshot_id": self.catalog_snapshot_id,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CatalogSnapshot":
        entries = tuple(CatalogEntry.from_dict(item) for item in payload["entries"])
        rebuilt = cls.build(entries)
        if payload.get("catalog_snapshot_id") != rebuilt.catalog_snapshot_id:
            raise LeagueContractError("catalog snapshot hash mismatch")
        return rebuilt

    def by_role(self, *roles: str) -> tuple[CatalogEntry, ...]:
        unknown = set(roles) - _ROLES
        if unknown:
            raise LeagueContractError(f"unknown roles: {sorted(unknown)}")
        return tuple(
            entry for entry in self.entries if entry.enabled and entry.role in roles
        )

    def get_instance(self, opponent_instance_id: str) -> CatalogEntry:
        for entry in self.entries:
            if entry.opponent_instance_id == opponent_instance_id:
                return entry
        raise LeagueContractError(
            f"unknown opponent_instance_id: {opponent_instance_id}"
        )
