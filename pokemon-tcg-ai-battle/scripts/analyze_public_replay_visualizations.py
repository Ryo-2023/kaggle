"""Summarize exact decks only when a public Replay ``visualize`` frame exposes them.

The reader deliberately ignores ``observation``, logs, search payloads, and
private zones.  A deck is recorded only when a visualization frame contains
two literal, integer-only 60-card arrays.  This makes a missing or malformed
visualization an explicit partial result rather than a reconstructed deck.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence


def deck_hash(cards: Sequence[int]) -> str:
    return hashlib.sha256(("\n".join(str(card) for card in cards) + "\n").encode()).hexdigest()


def _exact_decks_from_visualize(payload: Mapping[str, Any]) -> tuple[list[list[int]] | None, int]:
    """Return the first public visualization frame with two exact decks."""
    frames_seen = 0
    steps = payload.get("steps")
    if not isinstance(steps, Sequence):
        return None, frames_seen
    for step in steps:
        if not isinstance(step, Sequence) or not step or not isinstance(step[0], Mapping):
            continue
        visualize = step[0].get("visualize")
        if not isinstance(visualize, Sequence):
            continue
        for frame in visualize:
            if not isinstance(frame, Mapping):
                continue
            frames_seen += 1
            action = frame.get("action")
            if not isinstance(action, Sequence) or len(action) != 2:
                continue
            decks: list[list[int]] = []
            for cards in action:
                if not isinstance(cards, Sequence) or len(cards) != 60:
                    break
                if any(type(card) is not int for card in cards):
                    break
                decks.append(list(cards))
            if len(decks) == 2:
                return decks, frames_seen
    return None, frames_seen


def _winner_seat(payload: Mapping[str, Any], decks: list[list[int]] | None) -> int | None:
    rewards = payload.get("rewards")
    if isinstance(rewards, Sequence) and len(rewards) == 2 and all(type(value) is int for value in rewards):
        if rewards[0] != rewards[1]:
            return 0 if rewards[0] > rewards[1] else 1
    if decks is None:
        return None
    # The terminal result is only read from the same public visualization.
    for step in reversed(payload.get("steps", [])):
        if not isinstance(step, Sequence) or not step or not isinstance(step[0], Mapping):
            continue
        visualize = step[0].get("visualize")
        if not isinstance(visualize, Sequence):
            continue
        for frame in reversed(visualize):
            current = frame.get("current") if isinstance(frame, Mapping) else None
            result = current.get("result") if isinstance(current, Mapping) else None
            if result in (0, 1):
                return result
    return None


def analyze_payload(path: Path) -> tuple[dict[str, object], list[dict[str, object]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"Replay is not an object: {path}")
    # Kaggle's envelope ``id`` is a UUID; the public episode identifier is in
    # the filename used by the acquisition checkpoint.  Keep both instead of
    # silently substituting one identity for the other.
    episode_id = path.stem.removeprefix("episode-").removesuffix("-replay")
    info = payload.get("info") if isinstance(payload.get("info"), Mapping) else {}
    teams = info.get("TeamNames") if isinstance(info.get("TeamNames"), Sequence) else []
    decks, frames_seen = _exact_decks_from_visualize(payload)
    winner = _winner_seat(payload, decks)
    status = "EXACT_60_PUBLIC_VISUALIZE" if decks else "NO_EXACT_PUBLIC_VISUALIZE"
    episode = {
        "episode_id": episode_id,
        "replay_envelope_id": payload.get("id"),
        "raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "team_names": json.dumps(list(teams), ensure_ascii=False),
        "visualize_frames_seen": frames_seen,
        "exact_deck_status": status,
        "winner_seat": winner,
        "source": str(path),
    }
    rows: list[dict[str, object]] = []
    for seat, cards in enumerate(decks or []):
        rows.append({
            "episode_id": episode_id,
            "seat": seat,
            "team_name": teams[seat] if seat < len(teams) and isinstance(teams[seat], str) else None,
            "deck_hash": deck_hash(cards),
            "card_count": len(cards),
            "cards_json": json.dumps(cards),
            "result": "WIN" if winner == seat else "LOSS" if winner in (0, 1) else "UNKNOWN",
            "evidence": "public_visualize.action[seat]",
            "exact_deck_status": status,
        })
    return episode, rows


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields = sorted({key for row in rows for key in row}) or ["status"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    episodes: list[dict[str, object]] = []
    decks: list[dict[str, object]] = []
    for path in sorted(args.raw_dir.glob("episode-*-replay.json")):
        episode, rows = analyze_payload(path)
        episodes.append(episode)
        decks.extend(rows)
    write_csv(args.output / "replay_visualize_episode_registry.csv", episodes)
    write_csv(args.output / "replay_visualize_exact_deck_registry.csv", decks)
    summary = {
        "replays_analyzed": len(episodes),
        "replays_with_exact_public_visualize_decks": sum(row["exact_deck_status"] == "EXACT_60_PUBLIC_VISUALIZE" for row in episodes),
        "exact_60_decks": len(decks),
        "unique_exact_60_decks": len({row["deck_hash"] for row in decks}),
        "team_counts": dict(sorted(Counter(str(row["team_name"]) for row in decks).items())),
    }
    (args.output / "replay_visualize_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
