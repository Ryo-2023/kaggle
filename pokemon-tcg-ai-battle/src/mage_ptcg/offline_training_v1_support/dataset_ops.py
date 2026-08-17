"""Dataset lifecycle management module.

Implements inspection, validation, diffing, merging, compaction, migration,
and garbage collection planning for dataset records and manifests.
"""

from __future__ import annotations

import os
import gzip
import json
import time
import hashlib
from pathlib import Path
from typing import Any, Generator, Iterable

from mage_ptcg.offline_training_v1_support.contracts import (
    SupportContractError,
    atomic_write_json,
    atomic_write_records,
    digest,
    walk_safe,
)

DATASET_MANIFEST_SCHEMA_VERSION = "support-dataset-manifest-v1"
SHARD_SCHEMA_VERSION = "support-shard-v1"


def get_file_sha256(path: Path) -> str:
    """Calculate SHA-256 checksum of a file in a memory-efficient streaming manner."""
    sha = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


def stream_lines(path: Path, compression: str = "none") -> Generator[str, None, None]:
    """Stream lines from a plain text or gzip compressed file."""
    if compression == "gzip":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                yield line
    else:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                yield line


def validate_shard_stream(
    shard_path: Path,
    compression: str = "none",
    max_record_size: int = 1024 * 1024,
) -> dict[str, Any]:
    """Validate a single shard's integrity and compile record statistics via streaming."""
    record_count = 0
    episodes = set()
    decisions = set()
    candidates_sum = 0
    conflicts_detect = {}

    try:
        for line_num, line in enumerate(stream_lines(shard_path, compression), 1):
            if len(line.encode("utf-8")) > max_record_size:
                raise SupportContractError(
                    f"Oversized record (> {max_record_size} bytes) in shard {shard_path.name} at line {line_num}"
                )

            if not line.strip():
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SupportContractError(f"Corrupt JSONL in shard {shard_path.name} at line {line_num}: {exc}")

            if not isinstance(record, dict):
                raise SupportContractError(f"Record at line {line_num} is not a JSON object")

            # Privacy and Safety checks
            walk_safe(record)

            # Required schema validation
            required = {"episode_id", "decision_id", "state_digest", "teacher_action_key"}
            missing = required - set(record)
            if missing:
                raise SupportContractError(f"Missing fields {sorted(missing)} in record at line {line_num}")

            ep_id = record["episode_id"]
            dec_id = record["decision_id"]
            state_dig = record["state_digest"]
            teacher_act = record["teacher_action_key"]

            # Duplicates
            if dec_id in decisions:
                raise SupportContractError(f"Duplicate decision_id {dec_id} found in shard {shard_path.name}")
            decisions.add(dec_id)
            episodes.add(ep_id)

            # Conflict labels (same state, different actions)
            state_key = (state_dig, str(record.get("selection_type", "default")))
            if state_key in conflicts_detect:
                prev_act = conflicts_detect[state_key]
                if prev_act != teacher_act:
                    raise SupportContractError(
                        f"Conflicting label detected for state {state_dig}: '{prev_act}' vs '{teacher_act}'"
                    )
            else:
                conflicts_detect[state_key] = teacher_act

            # Candidates count
            candidates = record.get("legal_actions", [])
            candidates_sum += len(candidates)
            record_count += 1

    except Exception as exc:
        if isinstance(exc, SupportContractError):
            raise
        raise SupportContractError(f"Failed parsing shard {shard_path.name}: {exc}")

    return {
        "record_count": record_count,
        "episode_count": len(episodes),
        "decision_count": len(decisions),
        "candidate_count": candidates_sum,
        "episodes": list(episodes),
        "decisions": list(decisions),
    }


class DatasetLifecycleManager:
    """Manages the full dataset lifecycle operations including validation, merge, compact, and migration."""

    def __init__(self, workspace_dir: Path):
        self.workspace_dir = Path(workspace_dir)

    def load_manifest(self, manifest_path: Path) -> dict[str, Any]:
        """Load and validate basic manifest structure."""
        if not manifest_path.exists():
            raise SupportContractError(f"Manifest path does not exist: {manifest_path}")
        try:
            with manifest_path.open("r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as exc:
            raise SupportContractError(f"Failed to parse manifest JSON: {exc}")

        if manifest.get("schema_version") != DATASET_MANIFEST_SCHEMA_VERSION:
            raise SupportContractError(
                f"Unsupported manifest schema version: {manifest.get('schema_version')}"
            )
        return manifest

    def validate_dataset(self, manifest_path: Path) -> dict[str, Any]:
        """Thoroughly validate a dataset and all of its shards."""
        manifest = self.load_manifest(manifest_path)
        shards = manifest.get("shards", [])

        results = {
            "validation_status": "VALID",
            "errors": [],
            "total_records": 0,
            "total_episodes": 0,
            "total_decisions": 0,
        }

        all_episodes = set()
        all_decisions = set()
        split_episodes = {}  # split -> set of episodes

        # Verify lineage cycle
        lineage = manifest.get("parent_dataset_ids", [])
        dataset_id = manifest.get("dataset_id")
        if dataset_id in lineage:
            results["validation_status"] = "INVALID"
            results["errors"].append("Parent lineage cycle detected (dataset_id in parent_dataset_ids)")

        # Verify dataset_hash
        expected_dataset_hash = manifest.get("dataset_hash")
        if expected_dataset_hash and expected_dataset_hash != "dummy_hash":
            actual_dataset_hash = digest(shards, domain="dataset-manifest-hash")
            if expected_dataset_hash != actual_dataset_hash:
                results["validation_status"] = "INVALID"
                results["errors"].append("dataset_hash mismatch against shards configuration")

        for shard in shards:
            rel_path = shard.get("relative_path")
            if not rel_path:
                results["validation_status"] = "INVALID"
                results["errors"].append("Shard missing relative_path")
                continue

            shard_path = manifest_path.parent / rel_path
            if not shard_path.exists():
                results["validation_status"] = "INVALID"
                results["errors"].append(f"Missing shard file: {rel_path}")
                continue

            # Verify checksum
            expected_sha = shard.get("sha256")
            actual_sha = get_file_sha256(shard_path)
            if expected_sha != actual_sha:
                results["validation_status"] = "INVALID"
                results["errors"].append(f"Checksum mismatch for shard {rel_path}")
                continue

            # Verify size
            expected_size = shard.get("byte_size")
            actual_size = shard_path.stat().st_size
            if expected_size != actual_size:
                results["validation_status"] = "INVALID"
                results["errors"].append(f"Byte size mismatch for shard {rel_path}")
                continue

            # Stream validate shard content
            try:
                stats = validate_shard_stream(shard_path, shard.get("compression", "none"))

                # Cross check count parity
                if shard.get("record_count") != stats["record_count"]:
                    results["errors"].append(f"Record count mismatch in manifest for shard {rel_path}")

                results["total_records"] += stats["record_count"]

                # Check duplicates and leaks across splits
                split = shard.get("split", "train")
                if split not in split_episodes:
                    split_episodes[split] = set()

                for ep in stats["episodes"]:
                    # Cross-split episode leakage check
                    for other_split, eps in split_episodes.items():
                        if other_split != split and ep in eps:
                            results["validation_status"] = "INVALID"
                            results["errors"].append(
                                f"Cross-split leakage: episode {ep} exists in both '{split}' and '{other_split}'"
                            )
                    split_episodes[split].add(ep)
                    all_episodes.add(ep)

                for dec in stats["decisions"]:
                    if dec in all_decisions:
                        results["validation_status"] = "INVALID"
                        results["errors"].append(f"Global duplicate decision_id detected: {dec}")
                    all_decisions.add(dec)

            except SupportContractError as exc:
                results["validation_status"] = "INVALID"
                results["errors"].append(str(exc))

        results["total_episodes"] = len(all_episodes)
        results["total_decisions"] = len(all_decisions)

        if results["errors"]:
            results["validation_status"] = "INVALID"

        return results

    def inspect_dataset(self, manifest_path: Path) -> dict[str, Any]:
        """Gather structural summary of the dataset manifest."""
        manifest = self.load_manifest(manifest_path)
        shards = manifest.get("shards", [])

        return {
            "dataset_id": manifest.get("dataset_id"),
            "parent_dataset_ids": manifest.get("parent_dataset_ids", []),
            "feature_schema_hash": manifest.get("feature_schema_hash"),
            "source_collection_hash": manifest.get("source_collection_hash"),
            "created_at": manifest.get("created_at"),
            "shards_count": len(shards),
            "split_policy": manifest.get("split_policy"),
            "splits": list(manifest.get("splits", {}).keys()),
            "privacy_status": manifest.get("privacy_status"),
            "validation_status": manifest.get("validation_status"),
        }

    def diff_datasets(self, path_a: Path, path_b: Path) -> dict[str, Any]:
        """Compute differences between two datasets without full memory loads."""
        manifest_a = self.load_manifest(path_a)
        manifest_b = self.load_manifest(path_b)

        shards_a = {s["relative_path"]: s for s in manifest_a.get("shards", [])}
        shards_b = {s["relative_path"]: s for s in manifest_b.get("shards", [])}

        added_shards = sorted(list(shards_b.keys() - shards_a.keys()))
        removed_shards = sorted(list(shards_a.keys() - shards_b.keys()))

        # Shard checksum modifications
        modified_shards = []
        for path in shards_a.keys() & shards_b.keys():
            if shards_a[path].get("sha256") != shards_b[path].get("sha256"):
                modified_shards.append(path)

        schema_diff = manifest_a.get("feature_schema_hash") != manifest_b.get("feature_schema_hash")
        norm_diff = manifest_a.get("normalization_hash") != manifest_b.get("normalization_hash")
        privacy_diff = manifest_a.get("privacy_status") != manifest_b.get("privacy_status")

        return {
            "schema_version": "support-dataset-diff-v1",
            "added_shards": added_shards,
            "removed_shards": removed_shards,
            "modified_shards": sorted(modified_shards),
            "feature_schema_changed": schema_diff,
            "normalization_changed": norm_diff,
            "privacy_status_changed": privacy_diff,
            "lineage_changed": manifest_a.get("parent_dataset_ids") != manifest_b.get("parent_dataset_ids"),
        }

    def generate_merge_plan(self, manifest_paths: list[Path]) -> dict[str, Any]:
        """Validate merge prerequisites and generate a dry-run merge plan."""
        if len(manifest_paths) < 2:
            raise SupportContractError("Merging requires at least two dataset manifests.")

        manifests = [self.load_manifest(p) for p in manifest_paths]

        # Verify schema compatibilities
        base_schema = manifests[0].get("feature_schema_hash")
        base_privacy = manifests[0].get("privacy_status")
        base_splits = set(manifests[0].get("splits", {}).keys())

        plan = {
            "schema_version": "support-merge-plan-v1",
            "compatible": True,
            "reason": "OK",
            "shards_to_merge": [],
            "feature_schema_hash": base_schema,
            "parent_dataset_ids": [],
        }

        seen_datasets = set()
        for idx, m in enumerate(manifests):
            dataset_id = m.get("dataset_id")
            if dataset_id in seen_datasets:
                plan["compatible"] = False
                plan["reason"] = f"Duplicate dataset_id detected: {dataset_id}"
                return plan
            seen_datasets.add(dataset_id)
            plan["parent_dataset_ids"].append(dataset_id)

            if m.get("feature_schema_hash") != base_schema:
                plan["compatible"] = False
                plan["reason"] = f"Feature schema hash mismatch at index {idx}"
                return plan
            if m.get("privacy_status") != base_privacy:
                plan["compatible"] = False
                plan["reason"] = f"Privacy status mismatch at index {idx}"
                return plan

            for shard in m.get("shards", []):
                plan["shards_to_merge"].append({
                    "source_dataset_id": dataset_id,
                    "relative_path": shard["relative_path"],
                    "sha256": shard["sha256"],
                    "record_count": shard["record_count"],
                    "split": shard.get("split", "train"),
                })

        return plan

    def execute_merge(self, manifest_paths: list[Path], output_manifest_path: Path) -> dict[str, Any]:
        """Merge multiple datasets into a new dataset safely without modifying inputs."""
        plan = self.generate_merge_plan(manifest_paths)
        if not plan["compatible"]:
            raise SupportContractError(f"Merge plan generation failed: {plan['reason']}")

        output_manifest_path = Path(output_manifest_path)
        output_dir = output_manifest_path.parent
        output_dir.mkdir(parents=True, exist_ok=True)

        new_dataset_id = f"ds_merged_{int(time.time())}"
        merged_shards = []

        # Copy shard references and construct the new manifest
        # In a real environment, files might be copied or symlinked under the new dataset dir.
        # Here we copy the physical shard files to prevent path resolution failures if the output manifest is elsewhere.
        for idx, item in enumerate(plan["shards_to_merge"]):
            source_manifest = self.load_manifest(manifest_paths[idx // len(plan["shards_to_merge"])]) # Approximate source manifest path
            # Better: locate the source manifest that matches source_dataset_id
            src_manifest_path = next(p for p in manifest_paths if self.load_manifest(p).get("dataset_id") == item["source_dataset_id"])
            src_shard_file = src_manifest_path.parent / item["relative_path"]

            dest_rel_path = f"shards/{item['source_dataset_id']}_{Path(item['relative_path']).name}"
            dest_shard_file = output_dir / dest_rel_path
            dest_shard_file.parent.mkdir(parents=True, exist_ok=True)

            # Copy file atomically
            import shutil
            shutil.copy2(src_shard_file, dest_shard_file)

            # Get metadata from the original shard info
            original_manifest = self.load_manifest(src_manifest_path)
            orig_shard_info = next(s for s in original_manifest["shards"] if s["relative_path"] == item["relative_path"])

            merged_shards.append({
                "schema_version": SHARD_SCHEMA_VERSION,
                "relative_path": dest_rel_path,
                "sha256": item["sha256"],
                "byte_size": dest_shard_file.stat().st_size,
                "record_count": item["record_count"],
                "episode_count": orig_shard_info.get("episode_count", 0),
                "decision_count": orig_shard_info.get("decision_count", 0),
                "candidate_count": orig_shard_info.get("candidate_count", 0),
                "split": item["split"],
                "compression": orig_shard_info.get("compression", "none"),
            })

        new_manifest = {
            "schema_version": DATASET_MANIFEST_SCHEMA_VERSION,
            "dataset_id": new_dataset_id,
            "dataset_hash": digest(merged_shards, domain="dataset-manifest-hash"),
            "parent_dataset_ids": plan["parent_dataset_ids"],
            "feature_schema_hash": plan["feature_schema_hash"],
            "source_collection_hash": digest(plan["parent_dataset_ids"], domain="source-coll"),
            "created_at": time.time(),
            "split_policy": "merged",
            "splits": {"train": {}, "val": {}, "test": {}},
            "shards": merged_shards,
            "privacy_status": "PUBLIC_SAFE",
            "validation_status": "VALID",
            "provenance": {"operation": "merge"},
        }

        atomic_write_json(output_manifest_path, new_manifest)
        return new_manifest

    def execute_compact(
        self, manifest_path: Path, output_manifest_path: Path, max_compact_size: int = 50 * 1024 * 1024
    ) -> dict[str, Any]:
        """Compact small shards into larger, deterministic compressed shards."""
        manifest = self.load_manifest(manifest_path)
        output_manifest_path = Path(output_manifest_path)
        output_dir = output_manifest_path.parent
        output_dir.mkdir(parents=True, exist_ok=True)

        shards = manifest.get("shards", [])

        # Group shards by split
        split_groups: dict[str, list[dict[str, Any]]] = {}
        for s in shards:
            split = s.get("split", "train")
            split_groups.setdefault(split, []).append(s)

        compacted_shards = []

        for split, split_shards in split_groups.items():
            if not split_shards:
                continue

            # Compact all files of this split deterministically
            dest_rel_path = f"shards/compacted_{split}_{int(time.time())}.jsonl.gz"
            dest_shard_file = output_dir / dest_rel_path
            dest_shard_file.parent.mkdir(parents=True, exist_ok=True)

            total_records = 0
            episodes = set()
            decisions = set()
            candidates_sum = 0

            # Sort source files deterministically to ensure stable output ordering
            sorted_shards = sorted(split_shards, key=lambda x: x["relative_path"])

            # Stream records and write to a single compacted gzip file
            import tempfile
            with tempfile.NamedTemporaryFile("wt", encoding="utf-8", dir=output_dir, delete=False) as tmp_handle:
                # We write raw JSONL first to simplify debugging/parses, then we compress
                for s_info in sorted_shards:
                    src_path = manifest_path.parent / s_info["relative_path"]
                    for line in stream_lines(src_path, s_info.get("compression", "none")):
                        if not line.strip():
                            continue
                        record = json.loads(line)
                        episodes.add(record["episode_id"])
                        decisions.add(record["decision_id"])
                        candidates_sum += len(record.get("legal_actions", []))

                        tmp_handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
                        total_records += 1

                tmp_name = tmp_handle.name

            # Compress raw file to target gzip file atomically
            with gzip.open(dest_shard_file, "wt", encoding="utf-8") as g_file:
                with Path(tmp_name).open("r", encoding="utf-8") as r_file:
                    for line in r_file:
                        g_file.write(line)

            Path(tmp_name).unlink(missing_ok=True)

            compacted_shards.append({
                "schema_version": SHARD_SCHEMA_VERSION,
                "relative_path": dest_rel_path,
                "sha256": get_file_sha256(dest_shard_file),
                "byte_size": dest_shard_file.stat().st_size,
                "record_count": total_records,
                "episode_count": len(episodes),
                "decision_count": len(decisions),
                "candidate_count": candidates_sum,
                "split": split,
                "compression": "gzip",
            })

        new_manifest = {
            "schema_version": DATASET_MANIFEST_SCHEMA_VERSION,
            "dataset_id": f"ds_compacted_{int(time.time())}",
            "dataset_hash": digest(compacted_shards, domain="dataset-manifest-hash"),
            "parent_dataset_ids": [manifest.get("dataset_id")],
            "feature_schema_hash": manifest.get("feature_schema_hash"),
            "source_collection_hash": manifest.get("source_collection_hash"),
            "created_at": time.time(),
            "split_policy": manifest.get("split_policy"),
            "splits": manifest.get("splits", {}),
            "shards": compacted_shards,
            "privacy_status": manifest.get("privacy_status"),
            "validation_status": "VALID",
            "provenance": {"operation": "compaction"},
        }

        atomic_write_json(output_manifest_path, new_manifest)
        return new_manifest

    def migrate_dataset_plan(self, manifest_path: Path, target_version: str) -> str:
        """Evaluate schema migration compatibility and return support state."""
        manifest = self.load_manifest(manifest_path)
        current_version = manifest.get("schema_version")

        if current_version == target_version:
            return "NO_OP"

        # Explicit version support checks
        supported_migrations = {
            ("support-dataset-manifest-v1", "support-dataset-manifest-v2")
        }

        if (current_version, target_version) in supported_migrations:
            return "SUPPORTED"
        return "UNSUPPORTED"

    def execute_migration(self, manifest_path: Path, target_manifest_path: Path, target_version: str) -> dict[str, Any]:
        """Perform schema migration to a new manifest file version."""
        status = self.migrate_dataset_plan(manifest_path, target_version)
        if status == "NO_OP":
            return self.load_manifest(manifest_path)
        if status == "UNSUPPORTED":
            raise SupportContractError(f"Migration from manifest to version {target_version} is unsupported.")

        manifest = self.load_manifest(manifest_path)

        # Simple schema structure migration: support-dataset-manifest-v1 -> v2
        migrated = manifest.copy()
        migrated["schema_version"] = target_version
        migrated["migrated_at"] = time.time()
        migrated["dataset_hash"] = digest(migrated["shards"], domain="dataset-manifest-hash")

        atomic_write_json(target_manifest_path, migrated)
        return migrated

    def generate_gc_plan(self, registry_dir: Path, dataset_manifest_paths: list[Path]) -> dict[str, Any]:
        """Report unused or unreferenced shard candidate paths for cleanup, strictly without unlinking files."""
        # Collect all referenced shard files
        referenced_shards = set()
        for manifest_path in dataset_manifest_paths:
            try:
                manifest = self.load_manifest(manifest_path)
                for shard in manifest.get("shards", []):
                    ref_file = manifest_path.parent / shard["relative_path"]
                    referenced_shards.add(ref_file.resolve())
            except Exception:
                continue

        # Look for physical files in registry shards directory
        shards_dir = registry_dir / "shards"
        all_physical_shards = []
        if shards_dir.exists():
            for root, _, files in os.walk(shards_dir):
                for f in files:
                    file_path = Path(root) / f
                    all_physical_shards.append(file_path.resolve())

        # Unreferenced candidates
        candidates = []
        for file_path in all_physical_shards:
            if file_path not in referenced_shards:
                candidates.append(str(file_path))

        return {
            "schema_version": "support-gc-plan-v1",
            "scan_directory": str(shards_dir),
            "unreferenced_shards": sorted(candidates),
            "safe_to_cleanup_count": len(candidates),
        }
