"""remote refs/local drops の refresh、qualification 境界、catalog snapshot 化。"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from mage_ptcg.opponent_ingest.pipeline import run_ingestion
from mage_ptcg.policy_learning.submitted_opponents import load_registry
from mage_ptcg.policy_learning.submitted_runtime import pin_snapshot

from .catalog import CatalogEntry, CatalogSnapshot
from .contracts import (
    LeagueContractError,
    atomic_write_json,
    content_id,
    file_sha256,
    load_json,
    utc_now,
)
from .role_ledger import (
    RoleLedger,
    extend_role_ledger,
    initialize_role_ledger,
)


CommandRunner = Callable[[Sequence[str], Path], None]


def _run_command(command: Sequence[str], cwd: Path) -> None:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        raise LeagueContractError(
            f"source refresh command failed: {' '.join(command[:3])}: "
            f"{completed.stderr.strip()[-500:]}"
        )


def refresh_sources(
    *,
    repo: Path,
    artifact_root: Path,
    ingest_config: Mapping[str, Any],
    fetch_remotes: Sequence[str] = (),
    command_runner: CommandRunner = _run_command,
    mode: str = "incremental",
) -> dict[str, Any]:
    """明示された remote だけ fetch し、その後 read-only ingestion を行う。"""

    repo = Path(repo)
    for remote in fetch_remotes:
        if not remote or remote.startswith("-"):
            raise LeagueContractError(f"unsafe git remote name: {remote!r}")
        command_runner(("git", "fetch", "--prune", remote), repo)
    report = run_ingestion(repo, Path(artifact_root), ingest_config, mode=mode)
    artifact_dir = Path(artifact_root) / "artifacts"
    tracked = [
        path
        for path in (
            artifact_dir / "source_registry.json",
            artifact_dir / "deck_asset_registry.jsonl",
            artifact_dir / "agent_asset_registry.jsonl",
            artifact_dir / "candidate_population.json",
        )
        if path.exists()
    ]
    identity = {
        "tracked_files": [
            {"name": path.name, "sha256": file_sha256(path)}
            for path in sorted(tracked)
        ],
        "fetch_remotes": list(fetch_remotes),
    }
    source_snapshot_id = content_id("source-snapshot-v1", identity)
    snapshot = {
        "schema_version": 1,
        "source_snapshot_id": source_snapshot_id,
        **identity,
        "ingestion_report": report,
        "created_at": utc_now(),
    }
    atomic_write_json(
        Path(artifact_root) / "source_snapshots" / f"{source_snapshot_id}.json",
        snapshot,
    )
    return snapshot


def build_qualified_submitted_catalog(
    *,
    repo: Path,
    qualification_ledger_path: Path,
    output_root: Path,
    deck_pool_path: Path | None = None,
    prior_role_ledger_path: Path | None = None,
    initial_role_map: Mapping[str, str] | None = None,
    new_role_counts: Mapping[str, int] | None = None,
    seed: int = 71_000,
) -> dict[str, Any]:
    """既存 qualification evidence を持つ assets だけ実行可能 catalog にする。"""

    assets = load_registry(repo, qualification_ledger_path, include_discovered=True)
    eligible = [
        asset for asset in assets if asset.qualification == "TRAINING_ELIGIBLE"
    ]
    if not eligible:
        raise LeagueContractError("no qualification-backed submitted assets")
    submitted_provisional = [
        CatalogEntry.from_submitted_asset(asset, role="TRAINING_ACTIVE")
        for asset in eligible
    ]
    deck_pool_entries: list[CatalogEntry] = []
    if deck_pool_path is not None:
        pool_path = Path(deck_pool_path)
        pool = load_json(pool_path)
        if pool.get("schema") != "r2d3-deck-opponent-pool-v1":
            raise LeagueContractError("unsupported deck opponent pool schema")
        pool_hash = str(pool.get("pool_hash", ""))
        if len(pool_hash) != 64:
            raise LeagueContractError("deck opponent pool has no valid pool_hash")
        rule_path = Path(repo) / "agents" / "rule_agent.py"
        rule_hash = file_sha256(rule_path)
        for item in pool.get("entries", []):
            deck_path = Path(str(item.get("deck_path", "")))
            if not deck_path.is_absolute():
                deck_path = pool_path.parent / deck_path
            if not deck_path.is_file():
                raise LeagueContractError(
                    f"deck opponent pool entry is missing deck file: {deck_path}"
                )
            deck_id = str(item.get("deck_id", ""))
            source_kind = str(item.get("source_kind", "UNKNOWN"))
            source_id = str(item.get("source_id", ""))
            deck_pool_entries.append(
                CatalogEntry(
                    asset_id=f"deck-pool/{deck_id}",
                    policy_id=rule_hash,
                    deck_id=deck_id,
                    source_id=f"deck-pool:{source_kind}:{source_id}",
                    policy_kind="rule_v0",
                    runtime_path="builtin:rule_v0",
                    deck_path=str(deck_path.resolve()),
                    policy_hash=rule_hash,
                    deck_hash=str(item.get("deck_hash", "")),
                    source_hash=content_id(
                        "deck-pool-source-v1",
                        {
                            "pool_hash": pool_hash,
                            "source_kind": source_kind,
                            "source_id": source_id,
                            "source_commit": item.get("source_commit"),
                            "episode_id": item.get("episode_id"),
                        },
                    ),
                    role="TRAINING_ACTIVE",
                    deck_family=(
                        "PUBLIC_LEADERBOARD"
                        if source_kind == "KAGGLE_PUBLIC_REPLAY"
                        else "TEAM_REMOTE"
                    ),
                    archetype_id=str(
                        item.get("archetype_id")
                        or item.get("team_name")
                        or source_kind
                    ),
                    runtime_config_hash=content_id(
                        "builtin-runtime-config-v1", "rule_v0"
                    ),
                )
            )
    provisional = [*submitted_provisional, *deck_pool_entries]
    prior = (
        RoleLedger.from_dict(load_json(prior_role_ledger_path))
        if prior_role_ledger_path and Path(prior_role_ledger_path).exists()
        else None
    )
    if prior is None and initial_role_map is not None:
        known_assets = {entry.asset_id for entry in submitted_provisional}
        unknown = set(initial_role_map) - known_assets
        if unknown:
            raise LeagueContractError(
                f"initial role map contains unknown assets: {sorted(unknown)}"
            )
        mapped_entries = [
            entry
            for entry in submitted_provisional
            if entry.asset_id in initial_role_map
        ]
        prior = initialize_role_ledger(mapped_entries, initial_role_map)
    ledger = extend_role_ledger(
        provisional,
        prior=prior,
        role_counts=new_role_counts,
        seed=seed,
    )
    role_by_asset = {
        asset_id: assignment.role
        for assignment in ledger.assignments
        for asset_id in assignment.asset_ids
    }
    snapshots_root = Path(output_root) / "runtime_snapshots"
    entries = []
    for asset, entry in zip(eligible, submitted_provisional, strict=True):
        destination = snapshots_root / asset.asset_id.replace("/", "__") / asset.source_commit
        manifest_path = destination / ".submitted_snapshot_manifest.json"
        if manifest_path.exists():
            manifest = load_json(manifest_path)
            if (
                manifest.get("policy_hash") != asset.policy_hash
                or manifest.get("deck_hash") != asset.deck_hash
                or manifest.get("source_commit") != asset.source_commit
            ):
                raise LeagueContractError(
                    f"pinned snapshot identity mismatch: {asset.asset_id}"
                )
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            manifest = pin_snapshot(repo, asset, destination)
        entries.append(
            replace(
                entry,
                runtime_path=str(manifest_path),
                deck_path=str(manifest["deck_path"]),
                role=role_by_asset[asset.asset_id],
            )
        )
    entries.extend(
        replace(entry, role=role_by_asset[entry.asset_id])
        for entry in deck_pool_entries
    )
    deck_path = Path(repo) / "deck.csv"
    builtin_specs = (
        ("rule-v0", "rule_v0", Path(repo) / "agents" / "rule_agent.py"),
        ("rule-v1", "rule_v1", Path(repo) / "agents" / "rule_agent_v1.py"),
    )
    for asset_id, policy_kind, policy_path in builtin_specs:
        entries.append(
            CatalogEntry(
                asset_id=asset_id,
                policy_id=file_sha256(policy_path),
                deck_id=file_sha256(deck_path),
                source_id=f"repository:{policy_path.relative_to(repo)}",
                policy_kind=policy_kind,
                runtime_path=f"builtin:{policy_kind}",
                deck_path=str(deck_path),
                policy_hash=file_sha256(policy_path),
                deck_hash=file_sha256(deck_path),
                source_hash=file_sha256(policy_path),
                role="BENCHMARK_VISIBLE",
                deck_family="CURRENT",
                archetype_id=policy_kind.upper(),
                runtime_config_hash=content_id(
                    "builtin-runtime-config-v1", policy_kind
                ),
            )
        )
    catalog = CatalogSnapshot.build(entries)
    output_root = Path(output_root)
    atomic_write_json(output_root / "role_ledger.json", ledger.to_dict())
    atomic_write_json(output_root / "catalog_snapshot.json", catalog.to_dict())
    report = {
        "schema_version": 1,
        "catalog_snapshot_id": catalog.catalog_snapshot_id,
        "role_ledger_id": ledger.role_ledger_id,
        "eligible_assets": len(eligible),
        "deck_pool_entries": len(deck_pool_entries),
        "discovered_unqualified_assets": sum(
            asset.qualification != "TRAINING_ELIGIBLE" for asset in assets
        ),
        "role_counts": {
            role: sum(entry.role == role for entry in catalog.entries)
            for role in sorted({entry.role for entry in catalog.entries})
        },
        "automatic_promotion": False,
    }
    atomic_write_json(output_root / "catalog_build_report.json", report)
    return report
