"""Schema registry and schema compatibility manager.

Maintains registrations, schemas, compatibility checks, and migrations.
"""

from __future__ import annotations
from typing import Any
from mage_ptcg.offline_training_v1_support.json_schema import get_schema, validate_dict_by_schema
from mage_ptcg.offline_training_v1_support.contracts import digest

class SchemaRegistry:
    """Manages active schemas, registration, validation and compatibility checks."""

    def __init__(self) -> None:
        self.registry: dict[str, dict[str, Any]] = {}

        # Self-register known system schemas
        for name in ["game_result", "decision_diagnostic", "dataset_manifest", "model_manifest", "experiment_record"]:
            try:
                schema = get_schema(name)
                self.register(name, "v1", schema)
            except KeyError:
                pass

    def register(self, schema_id: str, version: str, schema: dict[str, Any], privacy_class: str = "PUBLIC") -> None:
        """Register a schema with metadata."""
        key = f"{schema_id}:{version}"
        self.registry[key] = {
            "schema_id": schema_id,
            "version": version,
            "schema": schema,
            "privacy_class": privacy_class,
            "hash": digest(schema, domain="schema-registry:v1")
        }

    def inspect(self, schema_id: str, version: str) -> dict[str, Any]:
        """Look up schema information."""
        key = f"{schema_id}:{version}"
        if key not in self.registry:
            raise KeyError(f"Schema registration not found for {key}")
        return self.registry[key]

    def validate(self, schema_id: str, version: str, data: dict[str, Any]) -> tuple[bool, str]:
        """Validate data against registered schema."""
        info = self.inspect(schema_id, version)
        return validate_dict_by_schema(data, info["schema"])

    def compare_schemas(self, schema_a: dict[str, Any], schema_b: dict[str, Any]) -> dict[str, Any]:
        """Compare two schemas and check for compatibility issues (e.g. breaking changes)."""
        # Checks if schema_b is backward compatible with schema_a
        issues = []

        req_a = set(schema_a.get("required", []))
        req_b = set(schema_b.get("required", []))

        # New required fields in B that did not exist in A are breaking
        new_reqs = req_b - req_a
        for nr in new_reqs:
            issues.append(f"Breaking change: New required field '{nr}' added in new version")

        props_a = schema_a.get("properties", {})
        props_b = schema_b.get("properties", {})

        # Field type changed
        for prop_name, prop_def_a in props_a.items():
            if prop_name in props_b:
                type_a = prop_def_a.get("type")
                type_b = props_b[prop_name].get("type")
                if type_a != type_b:
                    issues.append(f"Breaking change: Field '{prop_name}' changed type from {type_a} to {type_b}")

        return {
            "compatible": len(issues) == 0,
            "issues": issues
        }

    def check_compatibility(self, schema_id: str, version_a: str, version_b: str) -> dict[str, Any]:
        """Check compatibility between two versions of the same schema."""
        schema_a = self.inspect(schema_id, version_a)["schema"]
        schema_b = self.inspect(schema_id, version_b)["schema"]
        return self.compare_schemas(schema_a, schema_b)
