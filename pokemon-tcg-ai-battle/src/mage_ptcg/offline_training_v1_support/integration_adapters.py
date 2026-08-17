"""Integration adapters for converting Claude P0 artifacts to Support Platform formats.

Does not import any Claude codebase, working strictly on JSON/dict contracts.
"""

from __future__ import annotations

import math
from typing import Any
from mage_ptcg.offline_training_v1_support.contracts import digest, walk_safe, SupportContractError

ADAPTER_VERSION = "1.0.0"

class ClaudeIntegrationAdapter:
    """Adapts external Claude P0 JSON inputs into compatible Support Platform formats."""

    def __init__(self):
        self.version = ADAPTER_VERSION

    def _verify_privacy_and_finiteness(self, data: Any) -> str | None:
        """Run standard walk_safe on data to check for leaks or non-finite values."""
        try:
            walk_safe(data)
            return None
        except SupportContractError as exc:
            msg = str(exc)
            if "Forbidden key" in msg or "leak" in msg or "private" in msg or "Secret leaked" in msg:
                return "PRIVACY_REJECTED"
            if "Non-finite value" in msg:
                return "INCOMPATIBLE"
            return "INCOMPATIBLE"

    def adapt(self, artifact_type: str, data: dict[str, Any]) -> dict[str, Any]:
        """Adapt external dictionary to local schema formats.

        Supported types:
          - 'dataset_manifest'
          - 'game_record'
          - 'evaluation_summary'
        """
        # Ensure deep copy to prevent mutation
        import copy
        data = copy.deepcopy(data)

        source_hash = digest(data, domain="adapter-source")

        # Basic validation
        priv_status = self._verify_privacy_and_finiteness(data)
        if priv_status:
            return {
                "status": priv_status,
                "adapted_data": None,
                "warnings": ["Privacy violation or non-finite value detected"],
                "source_hash": source_hash,
                "adapter_version": self.version,
            }

        warnings = []
        status = "COMPATIBLE"

        # Schema dispatch
        if artifact_type == "dataset_manifest":
            # Map common Claude aliases to support schemas
            # e.g., 'dataset_name' -> 'dataset_id', 'files' -> 'shards'
            adapted = data.copy()
            if "dataset_name" in data and "dataset_id" not in data:
                adapted["dataset_id"] = data["dataset_name"]
                warnings.append("Mapped dataset_name to dataset_id")
                status = "COMPATIBLE_WITH_DEFAULTS"

            if "files" in data and "shards" not in data:
                adapted["shards"] = data["files"]
                warnings.append("Mapped files to shards")
                status = "COMPATIBLE_WITH_DEFAULTS"

            # Check required fields
            if "dataset_id" not in adapted:
                return {
                    "status": "MISSING_REQUIRED_FIELD",
                    "adapted_data": None,
                    "warnings": ["Missing required field: dataset_id"],
                    "source_hash": source_hash,
                    "adapter_version": self.version,
                }
            if "shards" not in adapted:
                return {
                    "status": "MISSING_REQUIRED_FIELD",
                    "adapted_data": None,
                    "warnings": ["Missing required field: shards"],
                    "source_hash": source_hash,
                    "adapter_version": self.version,
                }

            # Normalize shards
            normalized_shards = []
            for shard in adapted["shards"]:
                if "path" in shard and "relative_path" not in shard:
                    shard["relative_path"] = shard["path"]
                    warnings.append("Mapped shard path to relative_path")
                if "relative_path" not in shard:
                    return {
                        "status": "MISSING_REQUIRED_FIELD",
                        "adapted_data": None,
                        "warnings": ["Shard missing relative_path"],
                        "source_hash": source_hash,
                        "adapter_version": self.version,
                    }
                normalized_shards.append(shard)
            adapted["shards"] = normalized_shards

            # Standard defaults
            if "schema_version" not in adapted:
                adapted["schema_version"] = "support-dataset-manifest-v1"
                warnings.append("Added default schema_version")
                status = "COMPATIBLE_WITH_DEFAULTS"

            # Version check
            schema_ver = adapted.get("schema_version", "")
            if "v2" in schema_ver:
                return {
                    "status": "UNSUPPORTED_VERSION",
                    "adapted_data": None,
                    "warnings": [f"Unsupported future schema version: {schema_ver}"],
                    "source_hash": source_hash,
                    "adapter_version": self.version,
                }

        elif artifact_type == "game_record":
            # Map game record aliases
            # e.g., 'match_id' -> 'game_id', 'winner_id' -> 'winner', 'seat' -> 'candidate_seat'
            adapted = data.copy()
            if "match_id" in data and "game_id" not in data:
                adapted["game_id"] = data["match_id"]
                warnings.append("Mapped match_id to game_id")
                status = "COMPATIBLE_WITH_DEFAULTS"

            if "winner_id" in data and "winner" not in data:
                # Map external winner representation
                val = data["winner_id"]
                if val in ("candidate_policy", "candidate"):
                    adapted["winner"] = "candidate"
                elif val in ("opponent_policy", "opponent"):
                    adapted["winner"] = "opponent"
                else:
                    adapted["winner"] = "draw"
                warnings.append("Mapped winner_id to winner")
                status = "COMPATIBLE_WITH_DEFAULTS"

            if "seat" in data and "candidate_seat" not in data:
                adapted["candidate_seat"] = data["seat"]
                warnings.append("Mapped seat to candidate_seat")
                status = "COMPATIBLE_WITH_DEFAULTS"

            # Check required
            required = {"game_id", "winner", "candidate_seat"}
            missing = required - set(adapted)
            if missing:
                return {
                    "status": "MISSING_REQUIRED_FIELD",
                    "adapted_data": None,
                    "warnings": [f"Missing required fields: {sorted(list(missing))}"],
                    "source_hash": source_hash,
                    "adapter_version": self.version,
                }

        elif artifact_type == "evaluation_summary":
            # Map summary statistics
            adapted = data.copy()
            if "total" in data and "total_games" not in data:
                adapted["total_games"] = data["total"]
                warnings.append("Mapped total to total_games")
                status = "COMPATIBLE_WITH_DEFAULTS"

            if "total_games" not in adapted:
                return {
                    "status": "MISSING_REQUIRED_FIELD",
                    "adapted_data": None,
                    "warnings": ["Missing required field: total_games"],
                    "source_hash": source_hash,
                    "adapter_version": self.version,
                }

        else:
            return {
                "status": "INCOMPATIBLE",
                "adapted_data": None,
                "warnings": [f"Unsupported artifact type: {artifact_type}"],
                "source_hash": source_hash,
                "adapter_version": self.version,
            }

        return {
            "status": status,
            "adapted_data": adapted,
            "warnings": warnings,
            "source_hash": source_hash,
            "adapter_version": self.version,
        }
