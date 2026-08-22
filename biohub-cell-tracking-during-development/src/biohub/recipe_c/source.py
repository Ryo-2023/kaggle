"""Immutable provenance contracts for the public Biohub Recipe C assets.

The public V106 checkout, configuration, and two separately distributed model
artifacts are inputs to later campaign tasks.  This module only validates those
inputs and returns JSON-safe receipts; it never downloads, mutates, or stores an
absolute path to a credential directory.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

SOURCE_URL = "https://github.com/asapacsin/biohub-cell-tracking"
SOURCE_COMMIT = "843a47fdd531bdf7e6377673135519c54b69ae28"
SOURCE_LICENSE = "Apache-2.0"
LICENSE_RELATIVE_PATH = "LICENSE"
CONFIG_RELATIVE_PATH = "configs/experiments/recipe_c_motion_off_edge_0_40_det0_96875.yaml"
NOTEBOOK_RELATIVE_PATH = "upstream_clean_v106/clean-approach-lightweight-local-cv-no-hack.ipynb"
PREDICTOR_RELATIVE_PATH = "repo/scripts/predict_unet_transformer.py"
PREDICTOR_SHA256 = "c44e771ba5980b820f93091e03a303c25dfe8f3232e501f54dc9565731c234b9"
CHECKPOINT_RELATIVE_PATH = "weights/unet_transformer/split_0/edge_predictor_best.pth"
SECONDARY_STAGING_RELATIVE_PATH = "weights/unet_transformer/seed_314159/edge_predictor_best.pth"
PRIMARY_DATASET = "pilkwang/biohub-tracking-support-pack-50ep-v1"
SECONDARY_DATASET = "pilkwang/biohub-temporal-unet3d-seed314159-v1"
PRIMARY_DATASET_VERSION = 10
SECONDARY_DATASET_VERSION = 2
SUPPORT_DATASET_LICENSE = "CC0"
PRIMARY_CHECKPOINT_SHA256 = "12f6881ee3620a831697ca098ff8f48e687a24225f4e048b538deec3562fe771"
SECONDARY_CHECKPOINT_SHA256 = "9bac2fa0dadc4a6fc1899e0caf187f4b553e0a7cd90ba1261a68b35ffe9e305f"
LICENSE_SHA256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
CONFIG_SHA256 = "0e5758f3ea76ba015fb71c35bc749e136c009237e093d544a89a4b03a8c66ced"
NOTEBOOK_SHA256 = "5adc99aef3b61f2d8c5da5253eb1df13262986e8879bf6f630b5c1b5fa345d9d"

_HASH_CHUNK_SIZE = 1024 * 1024
_CONTRACT_PATH_FIELDS = (
    "license_relative_path",
    "config_relative_path",
    "notebook_relative_path",
    "predictor_relative_path",
    "primary_checkpoint_relative_path",
    "secondary_checkpoint_relative_path",
    "secondary_staging_relative_path",
)


@dataclass(frozen=True, slots=True)
class RecipeCSourceContract:
    """Source, configuration, and two-checkpoint identity for Recipe C."""

    source_url: str
    source_commit: str
    license: str = SOURCE_LICENSE
    license_relative_path: str = LICENSE_RELATIVE_PATH
    license_sha256: str = LICENSE_SHA256
    config_relative_path: str = CONFIG_RELATIVE_PATH
    config_sha256: str = CONFIG_SHA256
    notebook_relative_path: str = NOTEBOOK_RELATIVE_PATH
    notebook_sha256: str = NOTEBOOK_SHA256
    predictor_relative_path: str = PREDICTOR_RELATIVE_PATH
    predictor_sha256: str = PREDICTOR_SHA256
    primary_checkpoint_relative_path: str = CHECKPOINT_RELATIVE_PATH
    primary_checkpoint_sha256: str = PRIMARY_CHECKPOINT_SHA256
    secondary_checkpoint_relative_path: str = CHECKPOINT_RELATIVE_PATH
    secondary_checkpoint_sha256: str = SECONDARY_CHECKPOINT_SHA256
    secondary_staging_relative_path: str = SECONDARY_STAGING_RELATIVE_PATH
    primary_dataset: str = PRIMARY_DATASET
    primary_dataset_version: int = PRIMARY_DATASET_VERSION
    primary_dataset_license: str = SUPPORT_DATASET_LICENSE
    secondary_dataset: str = SECONDARY_DATASET
    secondary_dataset_version: int = SECONDARY_DATASET_VERSION
    secondary_dataset_license: str = SUPPORT_DATASET_LICENSE

    @property
    def source_license(self) -> str:
        """Alias used by receipts that name the license as source metadata."""

        return self.license

    @property
    def license_path(self) -> str:
        return self.license_relative_path

    @property
    def config_path(self) -> str:
        return self.config_relative_path

    @property
    def notebook_path(self) -> str:
        return self.notebook_relative_path

    @property
    def primary_checkpoint_path(self) -> str:
        return self.primary_checkpoint_relative_path

    @property
    def secondary_checkpoint_path(self) -> str:
        return self.secondary_checkpoint_relative_path


RECIPE_C_SOURCE = RecipeCSourceContract(
    source_url=SOURCE_URL,
    source_commit=SOURCE_COMMIT,
)


def canonical_json(payload: dict[str, object]) -> str:
    """Serialize a receipt deterministically without allowing non-JSON values."""

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_file(root: Path, relative_path: str, label: str) -> Path:
    """Resolve a file inside an original immutable source/support artifact.

    Run-local staging may intentionally use symlinks to external read-only assets;
    staged trees must not be passed to this original-artifact validator.
    """

    try:
        artifact_root = Path(root).resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{label} artifact root is missing: {root}") from exc
    if not artifact_root.is_dir():
        raise ValueError(f"{label} artifact root is not a directory: {root}")
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{label} path must be relative to the artifact root: {relative_path}")
    path = artifact_root / relative
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, RuntimeError) as exc:
        raise FileNotFoundError(f"{label} file is missing: {relative_path}") from exc
    if not resolved.is_relative_to(artifact_root):
        raise ValueError(f"{label} path escapes artifact root via symlink: {relative_path}")
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} file is missing: {relative_path}")
    return resolved


def _validate_contract_paths(contract: RecipeCSourceContract) -> None:
    for field in _CONTRACT_PATH_FIELDS:
        relative_path = getattr(contract, field)
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise ValueError(f"contract {field} must be a non-empty relative path")
        path = Path(relative_path)
        if path.is_absolute() or not path.parts or ".." in path.parts:
            raise ValueError(f"contract {field} must be a relative path under its root")


def _validate_file(root: Path, relative_path: str, expected_sha256: str, label: str) -> str:
    path = _artifact_file(root, relative_path, label)
    actual_sha256 = _sha256(path)
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"{label} SHA-256 mismatch for {relative_path}: "
            f"expected {expected_sha256}, got {actual_sha256}",
        )
    return actual_sha256


def _git_checkout_root(root: Path) -> Path:
    try:
        expected_root = Path(root).resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"source checkout root is missing: {root}") from exc
    if not expected_root.is_dir():
        raise ValueError(f"source checkout root is not a directory: {root}")

    result = subprocess.run(
        ["git", "-C", str(expected_root), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "not a Git checkout"
        raise ValueError(f"source checkout root could not be read: {detail}")
    try:
        actual_root = Path(result.stdout.strip()).resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError("source checkout root could not be resolved") from exc
    if actual_root != expected_root:
        raise ValueError(
            "source checkout root must be the exact Git top-level directory: "
            f"expected {expected_root}, got {actual_root}",
        )

    return expected_root


def _require_normal_git_index(root: Path) -> None:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-v", "-z"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip() or "unable to inspect source checkout index"
        raise ValueError(f"source checkout index could not be read: {detail}")
    for entry in result.stdout.split(b"\0"):
        if not entry:
            continue
        marker = chr(entry[0])
        relative = entry[2:].decode(errors="replace")
        if marker != "H":
            raise ValueError(
                "source checkout index flags are not allowed: "
                f"{marker} {relative}",
            )


def _require_clean_git_checkout(root: Path) -> None:
    status = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignored=no",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        detail = status.stderr.strip() or "unable to inspect source checkout status"
        raise ValueError(f"source checkout status could not be read: {detail}")
    if status.stdout.strip():
        changes = status.stdout.strip().replace("\n", "; ")
        raise ValueError(f"source checkout must be clean; modifications found: {changes}")


def _git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "not a Git checkout"
        raise ValueError(f"source commit could not be read at checkout: {detail}")
    return result.stdout.strip()


def _load_config_values(path: Path) -> dict[str, object]:
    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise RuntimeError("PyYAML is required to parse the pinned Recipe C config") from exc

    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"config must contain a mapping: {path.name}")
    return payload


def validate_source_checkout(
    root: Path,
    contract: RecipeCSourceContract = RECIPE_C_SOURCE,
) -> dict[str, object]:
    """Validate the pinned Git checkout and return an absolute-path-free receipt."""

    root = _git_checkout_root(Path(root))
    actual_commit = _git_head(root)
    if actual_commit != contract.source_commit:
        raise ValueError(
            f"source commit mismatch: expected {contract.source_commit}, got {actual_commit}",
        )
    _validate_contract_paths(contract)
    _require_normal_git_index(root)
    _require_clean_git_checkout(root)

    license_sha256 = _validate_file(
        root,
        contract.license_relative_path,
        contract.license_sha256,
        "license",
    )
    config_path = _artifact_file(root, contract.config_relative_path, "config")
    config_sha256 = _validate_file(
        root,
        contract.config_relative_path,
        contract.config_sha256,
        "config",
    )
    notebook_sha256 = _validate_file(
        root,
        contract.notebook_relative_path,
        contract.notebook_sha256,
        "notebook",
    )
    config_values = _load_config_values(
        config_path,
    )
    receipt: dict[str, object] = {
        "source_url": contract.source_url,
        "source_commit": actual_commit,
        "license": contract.license,
        "license_relative_path": contract.license_relative_path,
        "license_sha256": license_sha256,
        "config_relative_path": contract.config_relative_path,
        "config_sha256": config_sha256,
        "config_values": config_values,
        "notebook_relative_path": contract.notebook_relative_path,
        "notebook_sha256": notebook_sha256,
        "predictor_relative_path": contract.predictor_relative_path,
        "predictor_sha256": contract.predictor_sha256,
        "primary_checkpoint_relative_path": contract.primary_checkpoint_relative_path,
        "primary_checkpoint_sha256": contract.primary_checkpoint_sha256,
        "secondary_checkpoint_relative_path": contract.secondary_checkpoint_relative_path,
        "secondary_checkpoint_sha256": contract.secondary_checkpoint_sha256,
        "secondary_staging_relative_path": contract.secondary_staging_relative_path,
        "primary_dataset": contract.primary_dataset,
        "primary_dataset_version": contract.primary_dataset_version,
        "primary_dataset_license": contract.primary_dataset_license,
        "secondary_dataset": contract.secondary_dataset,
        "secondary_dataset_version": contract.secondary_dataset_version,
        "secondary_dataset_license": contract.secondary_dataset_license,
    }
    canonical_json(receipt)
    return receipt


def validate_support_artifacts(
    primary_root: Path,
    secondary_root: Path,
    contract: RecipeCSourceContract = RECIPE_C_SOURCE,
) -> dict[str, object]:
    """Validate the primary predictor and distinct primary/secondary checkpoints."""

    primary_root = Path(primary_root)
    secondary_root = Path(secondary_root)
    _validate_contract_paths(contract)
    if primary_root.resolve() == secondary_root.resolve():
        raise ValueError("primary and secondary support artifacts must use distinct roots")

    predictor_sha256 = _validate_file(
        primary_root,
        contract.predictor_relative_path,
        contract.predictor_sha256,
        "primary predictor",
    )
    primary_sha256 = _validate_file(
        primary_root,
        contract.primary_checkpoint_relative_path,
        contract.primary_checkpoint_sha256,
        "primary checkpoint",
    )
    secondary_sha256 = _validate_file(
        secondary_root,
        contract.secondary_checkpoint_relative_path,
        contract.secondary_checkpoint_sha256,
        "secondary checkpoint seed_314159",
    )
    if primary_sha256 == secondary_sha256:
        raise ValueError("primary and secondary checkpoints must be distinct")

    return {
        "primary_dataset": contract.primary_dataset,
        "primary_dataset_version": contract.primary_dataset_version,
        "primary_dataset_license": contract.primary_dataset_license,
        "secondary_dataset": contract.secondary_dataset,
        "secondary_dataset_version": contract.secondary_dataset_version,
        "secondary_dataset_license": contract.secondary_dataset_license,
        "predictor_relative_path": contract.predictor_relative_path,
        "predictor_sha256": predictor_sha256,
        "primary_checkpoint_relative_path": contract.primary_checkpoint_relative_path,
        "primary_checkpoint_sha256": primary_sha256,
        "secondary_checkpoint_relative_path": contract.secondary_checkpoint_relative_path,
        "secondary_checkpoint_sha256": secondary_sha256,
        "secondary_staging_relative_path": contract.secondary_staging_relative_path,
        "primary_checkpoint_root": "primary",
        "secondary_checkpoint_root": "secondary",
    }


__all__ = [
    "CONFIG_RELATIVE_PATH",
    "CONFIG_SHA256",
    "LICENSE_RELATIVE_PATH",
    "LICENSE_SHA256",
    "NOTEBOOK_RELATIVE_PATH",
    "NOTEBOOK_SHA256",
    "PREDICTOR_RELATIVE_PATH",
    "PREDICTOR_SHA256",
    "PRIMARY_CHECKPOINT_SHA256",
    "PRIMARY_DATASET",
    "PRIMARY_DATASET_VERSION",
    "RECIPE_C_SOURCE",
    "SECONDARY_CHECKPOINT_SHA256",
    "SECONDARY_DATASET",
    "SECONDARY_DATASET_VERSION",
    "SECONDARY_STAGING_RELATIVE_PATH",
    "SOURCE_COMMIT",
    "SOURCE_LICENSE",
    "SOURCE_URL",
    "SUPPORT_DATASET_LICENSE",
    "RecipeCSourceContract",
    "canonical_json",
    "validate_source_checkout",
    "validate_support_artifacts",
]
