#!/usr/bin/env python3
"""Describe, but do not execute, a frozen-residual fixed-six evaluation.

This research-only boundary validates one hash-bound residual sidecar against
one sealed preflight seed, then writes a dry-run evaluation descriptor.  It
does not import or invoke CABT.  ``--execute`` is intentionally fail-closed
until a separately reviewed engine integration and telemetry collector exist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.frozen_residual_factory_v1 import (  # noqa: E402
    FROZEN_RESIDUAL_POLICY_FACTORY_SCHEMA_V1,
)
from mage_ptcg.meta_specialist.frozen_residual_loader_v1 import (  # noqa: E402
    SIDECAR_ARTIFACT_SCHEMA_V1,
    load_frozen_residual_sidecar_v1,
)
from mage_ptcg.meta_specialist.frozen_residual_preflight_v1 import (  # noqa: E402
    FrozenResidualPreflightManifestV1,
    load_frozen_residual_preflight_manifest_v1,
)
from scripts.make_medal_opponents import EVAL_HELD_OUT_V1  # noqa: E402


FROZEN_RESIDUAL_STRENGTH_SCHEMA_V1 = "meta-specialist-frozen-residual-strength-v1"
_HEX64 = frozenset("0123456789abcdef")
_ZERO_COVERAGE_V1 = {
    "total_decisions": 0,
    "known_context": 0,
    "known_action": 0,
    "nonzero_residual": 0,
    "ood_pass_through": 0,
    "stop": 0,
}


def _sha256_file(path: Path, *, field: str) -> str:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"{field} must be a regular non-symlink file: {path}")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ValueError(f"{field} cannot be read: {path}") from exc
    return digest.hexdigest()


def _require_sha256(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(character not in _HEX64 for character in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 string")
    return value


def _canonical_sha256(value: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"),
    ).hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--sidecar-sha256", required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--preflight-sha256", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--base-deck-csv", type=Path, required=True)
    parser.add_argument("--base-deck-sha256", required=True)
    parser.add_argument("--base-archetype-id", required=True)
    parser.add_argument("--games-per-cell", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--execute", action="store_true",
        help="reserved for a future CABT implementation; this version fails closed",
    )
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    _require_sha256(args.sidecar_sha256, field="sidecar SHA-256")
    _require_sha256(args.preflight_sha256, field="preflight SHA-256")
    _require_sha256(args.base_deck_sha256, field="base deck SHA-256")
    if type(args.seed) is not int or args.seed not in {0, 1}:
        raise ValueError("--seed must be exactly 0 or 1")
    if type(args.base_archetype_id) is not str or not args.base_archetype_id:
        raise ValueError("--base-archetype-id must be nonempty")
    if type(args.games_per_cell) is not int or not 1 <= args.games_per_cell <= 2:
        raise ValueError("--games-per-cell must be between 1 and 2")
    _sha256_file(args.sidecar, field="sidecar")
    _sha256_file(args.preflight, field="preflight")
    actual_deck_sha = _sha256_file(args.base_deck_csv, field="base deck")
    if actual_deck_sha != args.base_deck_sha256:
        raise ValueError("base deck bytes do not match --base-deck-sha256")


def _seed_domain(manifest: FrozenResidualPreflightManifestV1, seed: int) -> object:
    domain = next((item for item in manifest.seeds if item.provenance.seed == seed), None)
    if domain is None:
        raise ValueError("preflight manifest does not contain the requested seed")
    return domain


def build_dry_run_descriptor_v1(args: argparse.Namespace) -> dict[str, Any]:
    """Validate closed identities and return the CABT-free evaluation plan."""
    _validate_args(args)
    manifest = load_frozen_residual_preflight_manifest_v1(
        args.preflight,
        expected_sha256=args.preflight_sha256,
        verify_files=False,
    )
    if manifest.subject_deck_sha256 != args.base_deck_sha256:
        raise ValueError("base deck SHA-256 differs from frozen preflight subject deck")
    domain = _seed_domain(manifest, args.seed)
    sidecar = load_frozen_residual_sidecar_v1(
        args.sidecar,
        expected_sidecar_sha256=args.sidecar_sha256,
        preflight_manifest=manifest,
        seed=args.seed,
    )
    if (
        sidecar.base_checkpoint_file_sha256 != domain.provenance.checkpoint_file_sha256
        or sidecar.base_checkpoint_tensor_sha256 != domain.provenance.checkpoint_tensor_state_sha256
    ):
        raise ValueError("sidecar loader identity differs from preflight seed provenance")
    if (
        sidecar.known_context_ids != frozenset(domain.context_ids)
        or sidecar.known_action_keys != frozenset(domain.action_keys)
    ):
        raise ValueError("sidecar loader coverage differs from preflight seed domain")

    loader = {
        "schema_version": SIDECAR_ARTIFACT_SCHEMA_V1,
        "sidecar_file_sha256": args.sidecar_sha256,
        "seed": args.seed,
        "base_checkpoint_path": domain.provenance.checkpoint_path,
        "base_checkpoint_file_sha256": sidecar.base_checkpoint_file_sha256,
        "base_checkpoint_tensor_state_sha256": sidecar.base_checkpoint_tensor_sha256,
        "known_context_count": len(sidecar.known_context_ids),
        "known_action_count": len(sidecar.known_action_keys),
    }
    factory_identity_input = {
        "schema_version": FROZEN_RESIDUAL_POLICY_FACTORY_SCHEMA_V1,
        "sidecar_file_sha256": args.sidecar_sha256,
        "seed": args.seed,
        "base_checkpoint_file_sha256": sidecar.base_checkpoint_file_sha256,
        "base_checkpoint_tensor_state_sha256": sidecar.base_checkpoint_tensor_sha256,
    }
    factory = {
        **factory_identity_input,
        "identity_sha256": _canonical_sha256(factory_identity_input),
        "known_context_count": len(sidecar.known_context_ids),
        "known_action_count": len(sidecar.known_action_keys),
        "training_permitted": False,
        "promotion_authority": False,
        "longrun_allowed": False,
    }
    planned_cells = len(EVAL_HELD_OUT_V1) * 2
    return {
        "schema_version": FROZEN_RESIDUAL_STRENGTH_SCHEMA_V1,
        "execution": "DRY_RUN_NOT_EXECUTED",
        "cabt_invoked": False,
        "research_only": True,
        "performance_evidence": False,
        "training_permitted": False,
        "promotion_authority": False,
        "longrun_allowed": False,
        "engine_seed_supported": False,
        "pairing": "independent_stratified_not_game_paired",
        "base_deck": {
            "csv_path": str(args.base_deck_csv.resolve()),
            "file_sha256": args.base_deck_sha256,
            "archetype_id": args.base_archetype_id,
        },
        "preflight": {
            "path": str(args.preflight.resolve()),
            "file_sha256": args.preflight_sha256,
            "schema_version": manifest.schema_version,
        },
        "sidecar": {
            "path": str(args.sidecar.resolve()),
            "file_sha256": args.sidecar_sha256,
            "schema_version": SIDECAR_ARTIFACT_SCHEMA_V1,
        },
        "seed": args.seed,
        "loader": loader,
        "factory": factory,
        "fixed_held_out_opponent_ids": list(EVAL_HELD_OUT_V1),
        "games_per_cell": args.games_per_cell,
        "planned_cells": planned_cells,
        "planned_games": planned_cells * args.games_per_cell,
        "coverage": dict(_ZERO_COVERAGE_V1),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    descriptor = build_dry_run_descriptor_v1(args)
    if args.execute:
        raise RuntimeError("CABT execution is not implemented for frozen residual strength v1")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(descriptor, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(descriptor, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
