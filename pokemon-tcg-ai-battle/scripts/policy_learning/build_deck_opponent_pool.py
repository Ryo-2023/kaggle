#!/usr/bin/env python3
"""Build a local Rule-v0 deck-opponent pool from remote refs and public Replay."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from main import validate_deck


def deck_hash(cards: list[int]) -> str:
    canonical = "\n".join(str(card) for card in sorted(cards)) + "\n"
    return hashlib.sha256(canonical.encode()).hexdigest()


def git(repo: Path, *args: str, binary: bool = False) -> str | bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=not binary, check=False)
    if result.returncode:
        raise RuntimeError((result.stderr if not binary else result.stderr.decode(errors="replace")).strip())
    return result.stdout


def remote_rows(repo: Path) -> list[dict[str, Any]]:
    refs = str(
        git(
            repo,
            "for-each-ref",
            "--format=%(refname:short)",
            "refs/remotes/origin/agent",
            "refs/remotes/origin/agents",
            "refs/remotes/origin/dev",
        )
    ).splitlines()
    rows = []
    for ref in sorted(refs):
        commit = str(git(repo, "rev-parse", ref)).strip()
        paths = ["deck.csv"]
        if ref == "origin/dev" or ref.startswith("origin/dev/"):
            paths.extend(
                path
                for path in str(
                    git(repo, "ls-tree", "-r", "--name-only", ref)
                ).splitlines()
                if path.startswith("opponents/") and path.endswith("/deck.csv")
            )
        for path in sorted(set(paths)):
            result = subprocess.run(
                ["git", "-C", str(repo), "show", f"{ref}:{path}"],
                capture_output=True,
                check=False,
            )
            if result.returncode:
                continue
            try:
                cards = list(
                    validate_deck(
                        [
                            int(value)
                            for value in result.stdout.decode().splitlines()
                            if value.strip()
                        ]
                    )
                )
            except (UnicodeDecodeError, ValueError):
                continue
            source_id = ref if path == "deck.csv" else f"{ref}:{path}"
            rows.append({
                "source_kind": "TEAM_REMOTE_REF",
                "source_id": source_id,
                "source_commit": commit,
                "source_path": path,
                "deck_hash": deck_hash(cards),
                "deck_cards": cards,
                "rank": None,
                "team_name": None,
                "score": None,
                "submission_id": None,
                "episode_id": None,
                "policy_binding": "RULE_V0_DECK_ONLY",
            })
    return rows


def replay_rows(report: Path) -> tuple[str, list[dict[str, Any]]]:
    payload = json.loads(report.read_text(encoding="utf-8"))
    rows = []
    groups = list(payload.get("medal_tiers", {}).values())
    for row in (item for group in groups for item in group):
        if not isinstance(row.get("deck"), list):
            continue
        cards = list(validate_deck([int(card) for card in row["deck"]]))
        rows.append({
            "source_kind": "KAGGLE_PUBLIC_REPLAY", "source_id": f"episode:{row['episode_id']}",
            "source_commit": None, "deck_hash": deck_hash(cards), "deck_cards": cards,
            "rank": int(row["rank"]), "team_name": str(row["team_name"]), "score": float(row["score"]),
            "submission_id": int(row["submission_id"]), "episode_id": int(row["episode_id"]),
            "policy_binding": "RULE_V0_DECK_ONLY",
        })
    return str(payload["snapshot_utc"]), rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--leaderboard-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    snapshot, replay = replay_rows(args.leaderboard_report)
    candidates = [*remote_rows(args.repo), *replay]
    by_hash: dict[str, dict[str, Any]] = {}
    aliases: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        aliases.setdefault(row["deck_hash"], []).append({key: row[key] for key in (
            "source_kind", "source_id", "source_commit", "rank", "team_name", "score", "submission_id", "episode_id"
        )})
        previous = by_hash.get(row["deck_hash"])
        if previous is None or (
            row["source_kind"] == "KAGGLE_PUBLIC_REPLAY"
            and (previous["source_kind"] != "KAGGLE_PUBLIC_REPLAY" or row["rank"] < previous["rank"])
        ):
            by_hash[row["deck_hash"]] = row
    entries = []
    args.output.mkdir(parents=True, exist_ok=True)
    decks = args.output / "decks"; decks.mkdir(exist_ok=True)
    for digest, row in sorted(by_hash.items(), key=lambda item: (
        item[1]["rank"] is None, item[1]["rank"] if item[1]["rank"] is not None else 10**9, item[0]
    )):
        identifier = f"deck-{digest[:16]}"
        path = decks / f"{identifier}.txt"
        path.write_text("\n".join(str(card) for card in row["deck_cards"]) + "\n", encoding="utf-8")
        entries.append({**row, "opponent_id": f"rule-v0-{identifier}", "deck_id": identifier,
                        "deck_path": str(path.resolve()), "aliases": aliases[digest],
                        "card_count": len(row["deck_cards"]), "unique_card_ids": len(Counter(row["deck_cards"]))})
    document = {
        "schema": "r2d3-deck-opponent-pool-v1", "created_at": datetime.now(timezone.utc).isoformat(),
        "leaderboard_snapshot": snapshot, "policy_binding": "RULE_V0_DECK_ONLY",
        "candidate_sources": len(candidates), "unique_decks": len(entries), "entries": entries,
    }
    # Paths and wall-clock creation time are materialization metadata.  The
    # identity must remain stable when identical source bytes are rebuilt.
    identity = {
        "schema": document["schema"],
        "leaderboard_snapshot": snapshot,
        "policy_binding": document["policy_binding"],
        "entries": [
            {
                key: value
                for key, value in entry.items()
                if key not in {"deck_path"}
            }
            for entry in entries
        ],
    }
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    document["pool_hash"] = hashlib.sha256(canonical.encode()).hexdigest()
    (args.output / "opponent_deck_pool.json").write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"sources": len(candidates), "unique_decks": len(entries), "pool_hash": document["pool_hash"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
