"""Registry management module.

Implements dataset, model, experiment, deck, and opponent registries.
Uses local file-based storage with indices, logs, and atomic updates.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from mage_ptcg.offline_training_v1_support.contracts import (
    FileLock,
    SupportContractError,
    atomic_write_json,
    digest,
    load_records,
)


class BaseRegistry:
    """Base logic for a single entity type registry."""

    def __init__(self, dir_path: Path, schema_version: str, id_field: str):
        self.dir_path = Path(dir_path)
        self.dir_path.mkdir(parents=True, exist_ok=True)
        self.schema_version = schema_version
        self.id_field = id_field
        self.index_path = self.dir_path / "index.json"
        self.history_path = self.dir_path / "history.jsonl"
        self.lock_path = self.dir_path / "registry.lock"

    def _validate_record_base(self, record: dict[str, Any], required_keys: set[str]) -> None:
        if record.get("schema_version") != self.schema_version:
            raise SupportContractError(
                f"Schema version mismatch: expected {self.schema_version}, got {record.get('schema_version')}"
            )
        missing = required_keys - set(record)
        if missing:
            raise SupportContractError(f"Missing required fields: {missing}")

    def _reconstruct_index_from_history(self) -> dict[str, Any]:
        index = {}
        if not self.history_path.exists():
            return index
        try:
            records = load_records(self.history_path)
            for record in records:
                rid = record.get(self.id_field)
                if rid:
                    index[rid] = record
            return index
        except Exception as exc:
            raise SupportContractError(f"Failed to reconstruct index from history: {exc}")

    def _load_index(self) -> dict[str, Any]:
        try:
            if not self.index_path.exists():
                if self.history_path.exists():
                    return self._reconstruct_index_from_history()
                return {}
            content = self.index_path.read_text(encoding="utf-8")
            if not content.strip():
                if self.history_path.exists():
                    return self._reconstruct_index_from_history()
                return {}
            import json
            index = json.loads(content)
            if not isinstance(index, dict):
                raise ValueError("Index is not a dictionary")
            return index
        except Exception:
            if self.history_path.exists():
                return self._reconstruct_index_from_history()
            return {}

    def _write_index(self, index: dict[str, Any]) -> None:
        atomic_write_json(self.index_path, index)

    def _append_history(self, record: dict[str, Any]) -> None:
        import json
        from mage_ptcg.offline_training_v1_support.contracts import walk_safe
        walk_safe(record)
        with self.history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
            handle.flush()
            import os
            os.fsync(handle.fileno())

    def _check_dataset_lineage_cycle(self, index: dict[str, Any], current_id: str, parent_ids: list[str]) -> None:
        visited = set()
        def walk(pid: str):
            if pid == current_id:
                raise SupportContractError(f"Lineage cycle detected: dataset {current_id} depends on itself")
            if pid in visited:
                return
            visited.add(pid)
            if pid in index:
                parent_records = index[pid].get("parent_dataset_ids", [])
                for p in parent_records:
                    walk(p)
        for p in parent_ids:
            walk(p)

    def _check_model_lineage_cycle(self, index: dict[str, Any], current_id: str, parent_id: str) -> None:
        visited = set()
        def walk(pid: str):
            if pid == current_id:
                raise SupportContractError(f"Lineage cycle detected: model {current_id} depends on itself")
            if pid in visited:
                return
            visited.add(pid)
            if pid in index:
                parent = index[pid].get("parent_model_id")
                if parent:
                    walk(parent)
        walk(parent_id)

    def register(self, record_id: str, record: dict[str, Any], required_keys: set[str]) -> str:
        """Register a record under the lock."""
        self._validate_record_base(record, required_keys)
        record = record.copy()

        with FileLock(self.lock_path):
            index = self._load_index()

            # Lineage cycle checks
            if self.id_field == "dataset_id":
                parents = record.get("parent_dataset_ids", [])
                self._check_dataset_lineage_cycle(index, record_id, parents)
            elif self.id_field == "model_id":
                parent = record.get("parent_model_id")
                if parent:
                    self._check_model_lineage_cycle(index, record_id, parent)

            # Generate/validate content hash (excluding timeline/hashes metadata)
            clean_rec = {k: v for k, v in record.items() if k not in ("content_hash", "created_at", "updated_at")}
            content_hash = digest(clean_rec, domain=self.id_field)
            record["content_hash"] = content_hash

            now = time.time()
            if record_id in index:
                # Update existing record
                existing = index[record_id]
                record_clean = {k: v for k, v in record.items() if k not in ("content_hash", "created_at", "updated_at")}
                existing_clean = {k: v for k, v in existing.items() if k not in ("content_hash", "created_at", "updated_at")}

                if record_clean != existing_clean:
                    # Model specific stage transitions check
                    if self.id_field == "model_id":
                        old_stage = existing.get("stage")
                        new_stage = record.get("stage")
                        if old_stage in ("ARCHIVED", "REJECTED") and new_stage != old_stage:
                            raise SupportContractError(f"Cannot transition model from {old_stage} to {new_stage}")
                    else:
                        raise SupportContractError(f"Record {record_id} already exists with different payload")

                record["created_at"] = existing.get("created_at", now)
                record["updated_at"] = now
            else:
                record["created_at"] = record.get("created_at", now)
                record["updated_at"] = record.get("updated_at", now)

            # Store content-addressed file
            record_file = self.dir_path / f"{content_hash}.json"
            atomic_write_json(record_file, record)

            # Update index
            index[record_id] = record
            self._write_index(index)

            # Append history
            self._append_history(record)

        return content_hash

    def get(self, record_id: str) -> dict[str, Any] | None:
        """Retrieve a record by ID."""
        index = self._load_index()
        return index.get(record_id)

    def list_records(self) -> list[dict[str, Any]]:
        """List all current records."""
        index = self._load_index()
        return list(index.values())

    def validate_registry(self) -> list[str]:
        """Detect and report corruption or hash mismatches in the registry."""
        corruptions = []
        index = self._load_index()
        for rid, record in index.items():
            content_hash = record.get("content_hash")
            if not content_hash:
                corruptions.append(f"Record {rid} missing content_hash in index")
                continue

            # Verify file exists
            record_file = self.dir_path / f"{content_hash}.json"
            if not record_file.exists():
                corruptions.append(f"Content addressed file {content_hash}.json not found for record {rid}")
                continue

            # Verify file content hash
            try:
                import json
                file_content = record_file.read_text(encoding="utf-8")
                data = json.loads(file_content)
                calc_hash = digest({k: v for k, v in data.items() if k not in ("content_hash", "created_at", "updated_at")}, domain=self.id_field)
                if calc_hash != content_hash:
                    corruptions.append(f"Hash mismatch for record {rid}: expected {content_hash}, got {calc_hash}")
            except Exception as exc:
                corruptions.append(f"Failed to read/validate {content_hash}.json for record {rid}: {exc}")

        return corruptions

    def archive(self, record_id: str) -> None:
        """Mark record stage/status as ARCHIVED (never physically deleted)."""
        with FileLock(self.lock_path):
            index = self._load_index()
            if record_id not in index:
                raise SupportContractError(f"Record {record_id} not found to archive")

            record = index[record_id].copy()
            if "stage" in record:
                record["stage"] = "ARCHIVED"
            elif "status" in record:
                record["status"] = "ARCHIVED"
            else:
                record["status"] = "ARCHIVED"

            record["updated_at"] = time.time()
            clean_rec = {k: v for k, v in record.items() if k not in ("content_hash", "created_at", "updated_at")}
            content_hash = digest(clean_rec, domain=self.id_field)
            record["content_hash"] = content_hash

            # Save updated content-addressed file
            atomic_write_json(self.dir_path / f"{content_hash}.json", record)

            # Update index
            index[record_id] = record
            self._write_index(index)
            self._append_history(record)


class DatasetRegistry(BaseRegistry):
    """Dataset lineage and verification registry."""

    def __init__(self, root_dir: Path):
        super().__init__(root_dir / "dataset", "support-dataset-registry-v1", "dataset_id")
        self.required_keys = {
            "schema_version", "dataset_id", "parent_dataset_ids", "dataset_hash",
            "feature_schema_hash", "episode_count", "decision_count", "candidate_count",
            "split_hashes", "shard_hashes", "privacy_status", "validation_status",
            "source_collection_hash"
        }

    def register_dataset(self, record: dict[str, Any]) -> str:
        return self.register(record["dataset_id"], record, self.required_keys)


class ModelRegistry(BaseRegistry):
    """Model evaluation and benchmarking registry."""

    def __init__(self, root_dir: Path):
        super().__init__(root_dir / "model", "support-model-registry-v1", "model_id")
        self.required_keys = {
            "schema_version", "model_id", "model_hash", "parent_model_id",
            "dataset_hash", "feature_schema_hash", "architecture",
            "training_config_hash", "metrics", "runtime_benchmark", "package_hash", "stage"
        }

    def register_model(self, record: dict[str, Any]) -> str:
        # Validate state rules (no auto-promotion)
        stage = record.get("stage")
        valid_stages = {"TRAINING", "EVALUATED", "SCREENED", "PACKAGE_READY", "REJECTED", "ARCHIVED"}
        if stage not in valid_stages:
            raise SupportContractError(f"Invalid model stage: {stage}")
        return self.register(record["model_id"], record, self.required_keys)


class ExperimentRegistry(BaseRegistry):
    """Offline/screening run performance registry."""

    def __init__(self, root_dir: Path):
        super().__init__(root_dir / "experiment", "support-experiment-registry-v1", "run_id")
        self.required_keys = {
            "schema_version", "run_id", "git_commit", "config_hash",
            "dataset_hash", "model_hash", "environment_hash", "offline_metrics",
            "screening_metrics", "latency", "status", "started_at", "completed_at"
        }

    def register_experiment(self, record: dict[str, Any]) -> str:
        return self.register(record["run_id"], record, self.required_keys)


class DeckRegistry(BaseRegistry):
    """Legal deck list tracking registry."""

    def __init__(self, root_dir: Path):
        super().__init__(root_dir / "deck", "support-deck-registry-v1", "deck_id")
        self.required_keys = {
            "schema_version", "deck_id", "deck_hash", "version",
            "availability", "validation_status", "provenance"
        }

    def register_deck(self, record: dict[str, Any]) -> str:
        return self.register(record["deck_id"], record, self.required_keys)


class OpponentRegistry(BaseRegistry):
    """Opponent configurations tracking registry."""

    def __init__(self, root_dir: Path):
        super().__init__(root_dir / "opponent", "support-opponent-registry-v1", "opponent_id")
        self.required_keys = {
            "schema_version", "opponent_id", "config_hash", "version",
            "availability", "validation_status", "provenance"
        }

    def register_opponent(self, record: dict[str, Any]) -> str:
        return self.register(record["opponent_id"], record, self.required_keys)


class SupportRegistryManager:
    """Consolidated registry manager coordinating all sub-registries."""

    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.dataset = DatasetRegistry(self.root_dir)
        self.model = ModelRegistry(self.root_dir)
        self.experiment = ExperimentRegistry(self.root_dir)
        self.deck = DeckRegistry(self.root_dir)
        self.opponent = OpponentRegistry(self.root_dir)

    def get_registry(self, kind: str) -> BaseRegistry:
        """Get registry instance by kind name."""
        registries = {
            "dataset": self.dataset,
            "model": self.model,
            "experiment": self.experiment,
            "deck": self.deck,
            "opponent": self.opponent,
        }
        if kind not in registries:
            raise SupportContractError(f"Unknown registry kind: {kind}")
        return registries[kind]

    def compare_records(self, kind: str, id_a: str, id_b: str) -> dict[str, Any]:
        """Compare two records of the same registry, highlighting differences."""
        reg = self.get_registry(kind)
        rec_a = reg.get(id_a)
        rec_b = reg.get(id_b)
        if not rec_a or not rec_b:
            raise SupportContractError(f"One or both records not found: {id_a}, {id_b}")

        diffs = {}
        all_keys = set(rec_a.keys()) | set(rec_b.keys())
        for k in all_keys:
            val_a = rec_a.get(k)
            val_b = rec_b.get(k)
            if val_a != val_b:
                diffs[k] = {"record_a": val_a, "record_b": val_b}
        return diffs
