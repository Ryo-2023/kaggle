"""新しい team ref を commit pin し、両 seat CABT smoke で資格化する。"""

from __future__ import annotations

import csv
import hashlib
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

from main import make_rule_agent, read_deck_csv, validate_deck
from mage_ptcg.opponent_ingest.pipeline import audit_agent_text
from mage_ptcg.policy_learning.submitted_opponents import SubmittedAsset
from mage_ptcg.policy_learning.submitted_runtime import pin_snapshot

from .benchmark import ScheduledGame, SubjectDeck
from .cabt import CabtMatchExecutor
from .catalog import CatalogEntry
from .contracts import (
    LeagueContractError,
    atomic_write_json,
    content_id,
    file_sha256,
)


def _git(repo: Path, *args: str, binary: bool = False) -> str | bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=not binary,
        check=False,
    )
    if completed.returncode:
        detail = (
            completed.stderr
            if isinstance(completed.stderr, str)
            else completed.stderr.decode(errors="replace")
        )
        raise LeagueContractError(f"git read failed: {detail.strip()[-500:]}")
    return completed.stdout


def resolve_ref_asset(
    repo: Path, *, ref: str, asset_id: str | None = None
) -> tuple[SubmittedAsset, str]:
    """許可済み team namespace の ref を実行 byte identity へ解決する。"""

    repo = Path(repo)
    if ref.startswith(("origin/agent/", "origin/agents/")):
        resolved_asset_id = asset_id or ref.removeprefix("origin/")
        expected = ref.removeprefix("origin/")
        if resolved_asset_id != expected:
            raise LeagueContractError(
                f"asset_id must match agent ref: expected {expected}"
            )
        main_path = "main.py"
        deck_path = "deck.csv"
    elif ref == "origin/dev":
        if not asset_id or not asset_id.startswith("dev/"):
            raise LeagueContractError("origin/dev requires --asset-id dev/<name>")
        name = asset_id.split("/", 1)[1]
        if not name or "/" in name:
            raise LeagueContractError("dev asset name must be one path component")
        resolved_asset_id = asset_id
        main_path = f"opponents/{name}/main.py"
        deck_path = f"opponents/{name}/deck.csv"
    else:
        raise LeagueContractError(
            "qualification ref must be origin/agent/*, origin/agents/*, or origin/dev"
        )

    commit = str(_git(repo, "rev-parse", ref)).strip()
    main_bytes = bytes(_git(repo, "show", f"{commit}:{main_path}", binary=True))
    deck_bytes = bytes(_git(repo, "show", f"{commit}:{deck_path}", binary=True))
    try:
        cards = validate_deck(
            [int(value) for value in deck_bytes.decode().splitlines() if value.strip()]
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise LeagueContractError(f"qualified ref has invalid deck.csv: {exc}") from exc
    audit = audit_agent_text(
        main_bytes.decode("utf-8", errors="replace"),
        source_id=f"{ref}@{commit}",
        path=main_path,
    )
    # Feature flags read through ``os.environ`` are common in team agents and
    # do not by themselves execute external effects.  Network, subprocess,
    # writes, dynamic execution, syntax errors, and credential-shaped literals
    # remain blocking.
    blocking_findings = set(audit["static_findings"]) - {"environment"}
    if blocking_findings:
        raise LeagueContractError(
            f"qualified ref failed static audit: {sorted(blocking_findings)}"
        )
    policy_hash = hashlib.sha256(main_bytes).hexdigest()
    deck_hash = hashlib.sha256(deck_bytes).hexdigest()
    return (
        SubmittedAsset(
            asset_id=resolved_asset_id,
            ref=ref,
            source_commit=commit,
            submission_source_commit="",
            source_lineage=commit,
            exactness="CURRENT_REF_QUALIFICATION",
            deck_id="deck.csv",
            deck_hash=deck_hash,
            policy_id=resolved_asset_id,
            policy_hash=policy_hash,
            adapter_hash="",
            runtime_config_hash="",
            deck_family="UNKNOWN",
            entrypoint="main.py:agent",
            local_runtime_status="UNQUALIFIED",
            official_runtime_evidence=False,
            previous_smoke_evidence=False,
            previous_tournament_evidence=False,
            current_ref_commit=commit,
            ref_matches_source_commit=True,
            qualification="UNQUALIFIED",
        ),
        content_id("qualified-deck-cards-v1", cards),
    )


class _RuleRuntime:
    def __init__(self, deck: list[int]) -> None:
        self.deck = deck
        self.runtime_policy_id = content_id(
            "qualification-rule-runtime-v1", deck
        )

    def create(self, *, game_id: str, seat: int) -> Any:
        seed = int(content_id("qualification-rule-seed-v1", [game_id, seat])[:8], 16)
        return make_rule_agent(deck=self.deck, seed=seed)


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def _merge_qualification_ledger(
    *,
    base_ledger: Path | None,
    row: dict[str, Any],
    output_path: Path,
) -> None:
    existing: list[dict[str, Any]] = []
    fields: list[str] = []
    if base_ledger is not None:
        with Path(base_ledger).open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = list(reader.fieldnames or ())
            existing = list(reader)
    existing = [
        item for item in existing if item.get("asset_id") != row["asset_id"]
    ]
    for field in row:
        if field not in fields:
            fields.append(field)
    _write_csv(output_path, [*existing, row], fields)


def qualify_ref(
    *,
    repo: Path,
    ref: str,
    output_root: Path,
    asset_id: str | None = None,
    base_ledger: Path | None = None,
    games: int = 2,
    seed: int = 76_000,
    max_steps: int = 10_000,
) -> dict[str, Any]:
    """新しい ref を archive snapshot 化し、両 seat 合法実行後に台帳化する。"""

    if games < 2 or games % 2:
        raise LeagueContractError("qualification games must be an even integer >= 2")
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    asset, deck_cards_id = resolve_ref_asset(repo, ref=ref, asset_id=asset_id)
    snapshot_root = output_root / "snapshot"
    manifest = pin_snapshot(repo, asset, snapshot_root)
    entry = replace(
        CatalogEntry.from_submitted_asset(asset, role="TRAINING_RESERVE"),
        runtime_path=str(snapshot_root / ".submitted_snapshot_manifest.json"),
        deck_path=str(manifest["deck_path"]),
    )

    subject_deck_path = Path(repo) / "deck.csv"
    deck = list(read_deck_csv(subject_deck_path))
    runtime = _RuleRuntime(deck)
    subject = SubjectDeck(
        "qualification-rule-v0-deck",
        str(subject_deck_path.resolve()),
        file_sha256(subject_deck_path),
    )
    executor = CabtMatchExecutor(
        runtime_policy=runtime,
        subject_decks=(subject,),
        output_root=output_root / "matches",
        scratch_root=output_root / "scratch",
        max_steps=max_steps,
        save_failures_html=True,
    )
    results = []
    for index in range(games):
        seat = "subject_first" if index % 2 == 0 else "subject_second"
        game = ScheduledGame(
            benchmark_id=content_id(
                "qualification-benchmark-v1", asset.source_commit
            ),
            runtime_policy_id=runtime.runtime_policy_id,
            subject_deck_id=subject.deck_id,
            opponent_instance_id=entry.opponent_instance_id,
            seat=seat,
            repetition_index=index // 2,
            execution_block="ref-qualification",
            env_seed=seed + index,
            game_key=content_id(
                "qualification-game-v1",
                {
                    "source_commit": asset.source_commit,
                    "seat": seat,
                    "repetition": index // 2,
                    "seed": seed + index,
                },
            ),
        )
        result, _policy = executor.execute(game, entry)
        results.append({"seat": seat, **result})

    row: dict[str, Any] = {
        "asset_id": asset.asset_id,
        "ref": asset.ref,
        "branch_tip": asset.source_commit,
        "source_commit": asset.source_commit,
        "source_lineage": asset.source_lineage,
        "exactness": asset.exactness,
        "deck_id": asset.deck_id,
        "deck_hash": asset.deck_hash,
        "policy_id": asset.policy_id,
        "policy_hash": asset.policy_hash,
        "adapter_hash": asset.adapter_hash,
        "runtime_config_hash": asset.runtime_config_hash,
        "entrypoint": asset.entrypoint,
        "local_runtime_status": "PROXY_RUNTIME_PASSED",
        "smoke_games": games,
        "illegal": 0,
        "crash": 0,
        "timeout": 0,
        "official_runtime_evidence": False,
        "teacher_eligible": True,
        "calibration_eligible": False,
        "notes": "commit-pinned isolated worker; both seats; CABT legal",
    }
    result = {
        "schema_version": 1,
        "status": "TRAINING_ELIGIBLE",
        "asset": row,
        "opponent_instance_id": entry.opponent_instance_id,
        "deck_cards_id": deck_cards_id,
        "results": results,
        "remote_mutation": False,
    }
    atomic_write_json(output_root / "qualification_result.json", result)
    _write_csv(
        output_root / "qualification_row.csv",
        [row],
        list(row),
    )
    _merge_qualification_ledger(
        base_ledger=base_ledger,
        row=row,
        output_path=output_root / "submitted_asset_registry.csv",
    )
    return result


__all__ = ["qualify_ref", "resolve_ref_asset"]
