"""Reproducibility metadata bundle management module.

Packages and checks public-safe experiment configuration and evaluation summaries.
"""

from __future__ import annotations

import os
import tarfile
import tempfile
import time
import json
import hashlib
from pathlib import Path
from typing import Any

from mage_ptcg.offline_training_v1_support.contracts import (
    SupportContractError,
    atomic_write_json,
    digest,
)


def is_path_safe(path_str: str) -> bool:
    """Verify that a path is relative and does not attempt traversal using parent directory dots or Windows drive letters."""
    if ":" in path_str or "\\" in path_str:
        return False
    p = Path(path_str)
    if p.is_absolute():
        return False
    if ".." in p.parts:
        return False
    return True


def redact_sensitive_strings(val: Any) -> Any:
    """Sanitize secrets, absolute directory paths, and usernames recursively."""
    if isinstance(val, dict):
        return {k: redact_sensitive_strings(v) for k, v in val.items()}
    elif isinstance(val, (list, tuple)):
        return [redact_sensitive_strings(item) for item in val]
    elif isinstance(val, str):
        # Exclude paths
        for p in ("/home/", "/mnt/", "/Users/", "C:\\"):
            if p in val:
                return "[PATH_REDACTED]"
        # Exclude secrets
        for secret in ("oauth", "token", "cookie", "Authorization", "Bearer", "api_key"):
            if secret.lower() in val.lower():
                return "[SECRET_REDACTED]"
        # Exclude raw digests
        if len(val) == 64 and all(c in "0123456789abcdef" for c in val):
            return "[DIGEST_REDACTED]"
    return val


class ReproducibilityBundleManager:
    """Assembles and verifies public-safe metadata tarballs ensuring traversal protection."""

    def __init__(self, workspace_dir: Path):
        self.workspace_dir = Path(workspace_dir)

    def assemble_bundle(
        self,
        output_tar: Path,
        metadata: dict[str, Any],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Collect, sanitize, and archive metadata files into a deterministic tarball."""
        output_tar = Path(output_tar)

        # Redact private fields
        clean_metadata = redact_sensitive_strings(metadata)

        # Missing evidence report
        missing_evidence = []
        expected_sections = {
            "resolved_config", "git_commit", "environment_summary",
            "dataset_manifest_summary", "model_manifest_summary", "evaluation_summary"
        }
        for sec in expected_sections:
            if sec not in clean_metadata:
                missing_evidence.append(sec)

        manifest_entries = {}

        # Add metadata files to a temporary workspace structure
        temp_files = {
            "metadata_manifest.json": clean_metadata,
            "system_info.json": {
                "assembled_at": 0.0,  # Normalize timestamp to 0.0 for deterministic archive bytes
                "schema_version": "support-reproducibility-manifest-v1",
            }
        }

        # Calculate per-file SHA-256
        for name, content in temp_files.items():
            serialized = json.dumps(content, sort_keys=True, ensure_ascii=False)
            file_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
            manifest_entries[name] = {"sha256": file_hash}

        bundle_manifest = {
            "schema_version": "support-reproducibility-manifest-v1",
            "files": manifest_entries,
            "missing_evidence_report": missing_evidence,
            "redaction_report": {
                "absolute_paths_excluded": True,
                "private_fields_redacted": True,
            }
        }

        # Write final bundle manifest to temp_files
        temp_files["bundle_manifest.json"] = bundle_manifest

        if dry_run:
            return bundle_manifest

        # Build tar archive deterministically
        output_tar.parent.mkdir(parents=True, exist_ok=True)

        # Filter function to ensure deterministic metadata in tarball
        def tar_filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo:
            tarinfo.mtime = 0
            tarinfo.uid = 0
            tarinfo.gid = 0
            tarinfo.uname = "root"
            tarinfo.gname = "root"
            tarinfo.mode = 0o644
            return tarinfo

        # We write temporary files to include in tar
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            # Sort keys for deterministic tar file ordering
            for name in sorted(temp_files.keys()):
                target_file = tmp_path / name
                with target_file.open("w", encoding="utf-8") as f:
                    json.dump(temp_files[name], f, sort_keys=True, ensure_ascii=False, indent=2)

            with tarfile.open(output_tar, "w:gz") as tar:
                for name in sorted(temp_files.keys()):
                    target_file = tmp_path / name
                    if not is_path_safe(name):
                        raise SupportContractError(f"Dangerous path traversal attempt: {name}")
                    tar.add(target_file, arcname=name, filter=tar_filter)

        # Get final bundle hash
        bundle_hash = hashlib.sha256()
        with output_tar.open("rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                bundle_hash.update(chunk)

        result_manifest = bundle_manifest.copy()
        result_manifest["bundle_sha256"] = bundle_hash.hexdigest()

        return result_manifest

    def verify_bundle(self, tar_path: Path) -> dict[str, Any]:
        """Verify the integrity and paths of a reproducibility bundle."""
        if not tar_path.exists():
            raise SupportContractError(f"Bundle file not found: {tar_path}")

        results = {
            "valid": True,
            "errors": [],
            "files": [],
        }

        seen_members = set()

        with tarfile.open(tar_path, "r:gz") as tar:
            # 1. Path traversal and link type protection checks
            for member in tar.getmembers():
                if member.name in seen_members:
                    results["valid"] = False
                    results["errors"].append(f"Security risk: Duplicate member detected: '{member.name}'")
                    return results
                seen_members.add(member.name)

                if not is_path_safe(member.name):
                    results["valid"] = False
                    results["errors"].append(f"Security risk: Path traversal detected in filename '{member.name}'")
                    return results

                # Reject symlinks and hardlinks
                if member.issym() or member.islnk():
                    results["valid"] = False
                    results["errors"].append(f"Security risk: Symlink or hardlink detected in filename '{member.name}'")
                    return results

                # Must be regular file
                if not member.isreg():
                    results["valid"] = False
                    results["errors"].append(f"Security risk: Non-regular file member: '{member.name}'")
                    return results

            # Extract bundle manifest
            try:
                manifest_file = tar.extractfile("bundle_manifest.json")
                if not manifest_file:
                    raise ValueError()
                manifest = json.loads(manifest_file.read().decode("utf-8"))
            except Exception:
                results["valid"] = False
                results["errors"].append("Missing or corrupt bundle_manifest.json inside the bundle.")
                return results

            # 2. Checksum validation for each file
            expected_files = manifest.get("files", {})
            for name in expected_files.keys():
                try:
                    f_obj = tar.extractfile(name)
                    if not f_obj:
                        results["valid"] = False
                        results["errors"].append(f"Expected file '{name}' missing from archive.")
                        continue

                    content = f_obj.read()
                    data = json.loads(content.decode("utf-8"))
                    serialized = json.dumps(data, sort_keys=True, ensure_ascii=False)
                    calc_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

                    if calc_hash != expected_files[name]["sha256"]:
                        results["valid"] = False
                        results["errors"].append(f"Checksum mismatch for file '{name}' in bundle.")
                except Exception as exc:
                    results["valid"] = False
                    results["errors"].append(f"Verification failure on '{name}': {exc}")

        return results
