#!/usr/bin/env python3
"""Analyze medal-zone and rival decks from public Kaggle CABT replays.

The public leaderboard identifies teams and ranks, while the first visualizer
state in a CABT replay contains both exact 60-card decks.  This script combines
the two sources, samples each medal tier and the teams around the user's rank,
then emits a Japanese Markdown report and machine-readable JSON.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import time
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
POKEMON_KINDS = {"Basic Pokémon", "Stage 1 Pokémon", "Stage 2 Pokémon"}
MEDAL_NAMES = {"gold": "金", "silver": "銀", "bronze": "銅"}
SUPPORT_FAMILIES = {"Dunsparce", "Fezandipiti ex", "Shaymin", "Munkidori", "Budew"}


@dataclass(frozen=True)
class LeaderboardEntry:
    rank: int
    team_id: int
    team_name: str
    submission_date: str
    score: str


def load_cards(en_path: Path, jp_path: Path) -> dict[int, dict[str, str]]:
    cards: dict[int, dict[str, str]] = {}
    with en_path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            card_id = int(row["Card ID"])
            cards.setdefault(
                card_id,
                {
                    "en": row["Card Name"],
                    "jp": row["Card Name"],
                    "kind": row["Stage (Pokémon)/Type (Energy and Trainer)"],
                    "previous": row["Previous stage"],
                    "expansion": row["Expansion"],
                    "number": row["Collection No."],
                },
            )
    with jp_path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            card_id = int(row["カード ID"])
            if card_id in cards:
                cards[card_id]["jp"] = row["カード名"]
    return cards


def iso_text(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _retryable(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) in {429, 500, 502, 503, 504}


def _call_with_retry(call: Any, *args: Any, attempts: int = 7, **kwargs: Any) -> Any:
    for attempt in range(attempts):
        try:
            return call(*args, **kwargs)
        except Exception as exc:
            if not _retryable(exc) or attempt == attempts - 1:
                raise
            delay = min(2**attempt, 30)
            time.sleep(delay)
    raise RuntimeError("unreachable")


def choose_submission(api: Any, leaderboard_row: Any) -> Any:
    submissions = _call_with_retry(api.competition_team_submissions, leaderboard_row.team_id) or []
    if not submissions:
        raise RuntimeError(f"No public submission for team {leaderboard_row.team_id}")
    # A simulation team is ranked by its best active submission; the row date
    # can point to a newer, lower-scoring submission.
    return max(submissions, key=lambda item: float(item.public_score or "-inf"))


def choose_episode(episodes: list[Any], submission_id: int) -> tuple[Any, int]:
    completed = [item for item in episodes if "COMPLETED" in str(item.state).upper()]
    if not completed:
        raise RuntimeError(f"No completed episode for submission {submission_id}")
    public = [item for item in completed if "PUBLIC" in str(item.type).upper()]
    episode = max(public or completed, key=lambda item: iso_text(item.create_time))
    agents = [item for item in episode.agents if item.submission_id == submission_id]
    if not agents:
        raise RuntimeError(f"Submission {submission_id} not found in episode {episode.id}")
    return episode, min(item.index for item in agents)


def extract_deck(replay: dict[str, Any], player_index: int) -> list[int]:
    first_step = replay.get("steps", [[]])[0]
    for agent in first_step:
        for visual in agent.get("visualize") or []:
            action = visual.get("action")
            if (
                isinstance(action, list)
                and len(action) > player_index
                and isinstance(action[player_index], list)
                and len(action[player_index]) == 60
            ):
                return [int(card_id) for card_id in action[player_index]]
    raise RuntimeError("Initial 60-card deck not found in replay visualization")


def medal_boundaries(team_count: int) -> dict[str, tuple[int, int]]:
    """Return inclusive Kaggle competition medal ranges for a team count."""
    if team_count <= 0:
        raise ValueError("team_count must be positive")
    if team_count < 100:
        gold_end = math.ceil(team_count * 0.10)
        silver_end = math.ceil(team_count * 0.20)
        bronze_end = math.ceil(team_count * 0.40)
    elif team_count < 250:
        gold_end = min(10, team_count)
        silver_end = math.ceil(team_count * 0.20)
        bronze_end = math.ceil(team_count * 0.40)
    elif team_count < 1000:
        gold_end = min(10 + team_count // 500, team_count)
        silver_end = min(50, team_count)
        bronze_end = min(100, team_count)
    else:
        gold_end = min(10 + team_count // 500, team_count)
        silver_end = math.ceil(team_count * 0.05)
        bronze_end = math.ceil(team_count * 0.10)
    return {
        "gold": (1, gold_end),
        "silver": (gold_end + 1, silver_end),
        "bronze": (silver_end + 1, bronze_end),
    }


def evenly_spaced_ranks(start: int, end: int, sample_size: int) -> list[int]:
    if start > end or sample_size <= 0:
        return []
    population = end - start + 1
    if sample_size >= population:
        return list(range(start, end + 1))
    if sample_size == 1:
        return [(start + end) // 2]
    selected = {
        round(start + index * (end - start) / (sample_size - 1))
        for index in range(sample_size)
    }
    # Rounding should normally preserve the requested size; fill any collision
    # deterministically for very narrow ranges.
    if len(selected) < sample_size:
        selected.update(rank for rank in range(start, end + 1) if rank not in selected)
    return sorted(selected)[:sample_size]


def parse_leaderboard_zip(path: Path) -> list[LeaderboardEntry]:
    with zipfile.ZipFile(path) as archive:
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(csv_names) != 1:
            raise RuntimeError(f"Expected one leaderboard CSV, found {len(csv_names)}")
        with archive.open(csv_names[0]) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
            return [
                LeaderboardEntry(
                    rank=int(row["Rank"]),
                    team_id=int(row["TeamId"]),
                    team_name=row["TeamName"],
                    submission_date=row["LastSubmissionDate"],
                    score=row["Score"],
                )
                for row in reader
            ]


def competition_metadata(api: Any, competition: str) -> dict[str, Any]:
    response = api.competitions_list(search=competition, page_size=20)
    matches = [
        item
        for item in (response.competitions if response else [])
        if str(item.ref).rstrip("/").split("/")[-1] == competition
    ]
    if not matches:
        raise RuntimeError(f"Competition metadata not found: {competition}")
    item = matches[0]
    return {
        "team_count": int(item.team_count),
        "user_rank": int(getattr(item, "user_rank", 0) or 0),
        "category": str(item.category),
        "awards_points": bool(getattr(item, "awards_points", False)),
    }


def _deck_cache_path(cache_dir: Path, team_id: int) -> Path:
    return cache_dir / f"team-{team_id}.json"


def _load_deck_cache(
    entry: LeaderboardEntry, cache_dir: Path, cache_hours: float, refresh: bool
) -> dict[str, Any] | None:
    path = _deck_cache_path(cache_dir, entry.team_id)
    if refresh or not path.is_file():
        return None
    if time.time() - path.stat().st_mtime > cache_hours * 3600:
        return None
    cached = json.loads(path.read_text(encoding="utf-8"))
    if len(cached.get("deck", [])) != 60:
        return None
    return {
        **cached,
        "rank": entry.rank,
        "team_id": entry.team_id,
        "team_name": entry.team_name,
        "score": entry.score,
        "submission_date": entry.submission_date,
        "cache_hit": True,
    }


def _seed_deck_cache(report_path: Path, cache_dir: Path, cache_hours: float) -> None:
    """Resume a partially successful previous run without repeating API calls."""
    if not report_path.is_file():
        return
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    try:
        snapshot = datetime.fromisoformat(payload["snapshot_utc"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return
    if time.time() - snapshot > cache_hours * 3600:
        return
    groups: list[Iterable[dict[str, Any]]] = list(payload.get("medal_tiers", {}).values())
    groups.append(payload.get("rivals", []))
    cache_dir.mkdir(parents=True, exist_ok=True)
    for row in (item for group in groups for item in group):
        if len(row.get("deck", [])) != 60:
            continue
        path = _deck_cache_path(cache_dir, int(row["team_id"]))
        if not path.is_file():
            path.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_deck_row(
    api: Any,
    entry: LeaderboardEntry,
    replay_dir: Path,
    deck_cache_dir: Path,
    cache_hours: float,
    refresh: bool,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "rank": entry.rank,
        "team_id": entry.team_id,
        "team_name": entry.team_name,
        "score": entry.score,
        "submission_date": entry.submission_date,
    }
    cached = _load_deck_cache(entry, deck_cache_dir, cache_hours, refresh)
    if cached is not None:
        return cached
    try:
        submission = choose_submission(api, entry)
        submission_id = int(submission.id)
        episodes = _call_with_retry(api.competition_list_episodes, submission_id)
        episode, player_index = choose_episode(episodes, submission_id)
        replay_path = replay_dir / f"episode-{episode.id}-replay.json"
        if not replay_path.is_file():
            _call_with_retry(api.competition_episode_replay, episode.id, str(replay_dir), quiet=True)
        replay = json.loads(replay_path.read_text(encoding="utf-8"))
        row.update(
            {
                "submission_id": submission_id,
                "episode_id": int(episode.id),
                "player_index": player_index,
                "deck": extract_deck(replay, player_index),
            }
        )
        deck_cache_dir.mkdir(parents=True, exist_ok=True)
        _deck_cache_path(deck_cache_dir, entry.team_id).write_text(
            json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    return row


def card_label(card_id: int, cards: dict[int, dict[str, str]]) -> str:
    card = cards.get(card_id, {})
    jp, en = card.get("jp", str(card_id)), card.get("en", str(card_id))
    name = jp if jp == en else f"{jp} / {en}"
    return f"{name} [{card.get('expansion', '?')} {card.get('number', '?')}; ID {card_id}]"


def pokemon_summary(deck: list[int], cards: dict[int, dict[str, str]]) -> str:
    counts = Counter(deck)
    pokemon = [
        (count, cards[card_id]["en"])
        for card_id, count in counts.items()
        if cards.get(card_id, {}).get("kind", "") in POKEMON_KINDS
    ]
    pokemon.sort(key=lambda item: (-item[0], item[1]))
    return ", ".join(f"{name}×{count}" for count, name in pokemon)


def _stage_level(kind: str) -> int:
    if kind == "Stage 2 Pokémon":
        return 2
    if kind == "Stage 1 Pokémon":
        return 1
    return 0


def _family_root(card_id: int, cards: dict[int, dict[str, str]]) -> str:
    card = cards[card_id]
    root = card["en"]
    previous = card.get("previous", "n/a")
    seen: set[str] = set()
    while previous and previous != "n/a" and previous not in seen:
        seen.add(previous)
        root = previous
        parent = next((value for value in cards.values() if value.get("en") == previous), None)
        previous = parent.get("previous", "n/a") if parent else "n/a"
    return root


def archetype_name(deck: list[int], cards: dict[int, dict[str, str]]) -> str:
    counts = Counter(deck)
    families: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "members": []})
    for card_id, count in counts.items():
        card = cards.get(card_id)
        if not card or card.get("kind") not in POKEMON_KINDS:
            continue
        family = families[_family_root(card_id, cards)]
        family["count"] += count
        family["members"].append((card_id, count))
    if not families:
        return "Pokémon不明"

    candidates = [item for item in families.items() if item[0] not in SUPPORT_FAMILIES]
    ranked = sorted(candidates or families.items(), key=lambda item: (-item[1]["count"], item[0]))
    largest = ranked[0][1]["count"]
    selected = [item for item in ranked[:2] if item[1]["count"] >= max(3, largest / 2)]
    labels: list[str] = []
    for _, family in selected:
        representative = max(
            family["members"],
            key=lambda item: (
                _stage_level(cards[item[0]].get("kind", "")),
                " ex" in cards[item[0]].get("en", ""),
                item[1],
            ),
        )[0]
        labels.append(cards[representative]["en"])
    return " / ".join(labels)


def deck_lines(deck: list[int], cards: dict[int, dict[str, str]]) -> list[str]:
    counts = Counter(deck)
    return [
        f"- {card_label(card_id, cards)} ×{counts[card_id]} (`{card_id}`)"
        for card_id in sorted(counts, key=lambda value: (cards.get(value, {}).get("kind", ""), value))
    ]


def trend_summary(rows: list[dict[str, Any]], cards: dict[int, dict[str, str]]) -> list[dict[str, Any]]:
    usable = [row for row in rows if row.get("deck")]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in usable:
        groups[archetype_name(row["deck"], cards)].append(row)
    result: list[dict[str, Any]] = []
    for name, members in groups.items():
        signatures = {tuple(sorted(Counter(row["deck"]).items())) for row in members}
        result.append(
            {
                "archetype": name,
                "count": len(members),
                "share": len(members) / len(usable) if usable else 0.0,
                "rank_min": min(row["rank"] for row in members),
                "rank_max": max(row["rank"] for row in members),
                "average_score": sum(float(row["score"]) for row in members) / len(members),
                "unique_decks": len(signatures),
            }
        )
    return sorted(result, key=lambda item: (-item["count"], item["rank_min"], item["archetype"]))


def _trend_sentence(rows: list[dict[str, Any]], cards: dict[int, dict[str, str]]) -> str:
    trends = trend_summary(rows, cards)[:3]
    if not trends:
        return "有効なデッキを取得できませんでした。"
    return "、".join(
        f"{item['archetype']} {item['count']}件 ({item['share']:.0%})" for item in trends
    )


def _append_trend_table(
    lines: list[str], rows: list[dict[str, Any]], cards: dict[int, dict[str, str]]
) -> None:
    lines += [
        "| アーキタイプ | 件数 | 比率 | 観測順位 | 平均Score | 異なる60枚 |",
        "| --- | ---: | ---: | --- | ---: | ---: |",
    ]
    for item in trend_summary(rows, cards):
        lines.append(
            f"| {item['archetype']} | {item['count']} | {item['share']:.1%} | "
            f"{item['rank_min']}–{item['rank_max']} | {item['average_score']:.1f} | "
            f"{item['unique_decks']} |"
        )


def _append_team_table(
    lines: list[str], rows: list[dict[str, Any]], cards: dict[int, dict[str, str]]
) -> None:
    lines += [
        "| 順位 | Team | Score | Submission | アーキタイプ | ポケモン構成 |",
        "| ---: | --- | ---: | ---: | --- | --- |",
    ]
    for row in rows:
        if row.get("error"):
            archetype, core = "取得エラー", row["error"]
        else:
            archetype = archetype_name(row["deck"], cards)
            core = pokemon_summary(row["deck"], cards)
        lines.append(
            f"| {row['rank']} | {row['team_name']} | {row['score']} | "
            f"{row.get('submission_id', '-')} | {archetype} | {core} |"
        )


def _append_gold_alakazam_comparison(
    lines: list[str],
    gold_rows: list[dict[str, Any]],
    own_deck: list[int],
    own_submission: int,
    cards: dict[int, dict[str, str]],
    alakazam_id: int,
) -> None:
    rows = [row for row in gold_rows if alakazam_id in row.get("deck", [])]
    lines += ["", "## 金メダル圏フーディン系との差分", ""]
    if not rows:
        lines.append("金メダル圏にフーディンを含む取得済みデッキはありません。")
        return
    own_counts = Counter(own_deck)
    all_ids = set(own_counts)
    for row in rows:
        all_ids.update(row["deck"])
    header = ["カード", f"自分 ({own_submission})"] + [
        f"#{row['rank']} {row['team_name']}" for row in rows
    ]
    lines += [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] + ["---:"] * (len(header) - 1)) + " |",
    ]
    for card_id in sorted(all_ids):
        values = [own_counts[card_id]] + [Counter(row["deck"])[card_id] for row in rows]
        if len(set(values)) > 1:
            lines.append(
                "| " + " | ".join([card_label(card_id, cards)] + [str(value) for value in values]) + " |"
            )

    energy_ids = {card_id for card_id, card in cards.items() if "Energy" in card.get("kind", "")}
    own_energy = sum(own_counts[card_id] for card_id in energy_ids)
    gold_energy = [sum(Counter(row["deck"])[card_id] for card_id in energy_ids) for row in rows]
    signatures = {tuple(sorted(Counter(row["deck"]).items())) for row in rows}
    lines += [
        "",
        "### 差分の要点",
        "",
        f"- 金メダル圏{len(gold_rows)}件中{len(rows)}件がフーディンを採用し、"
        f"60枚リストは{len(signatures)}種類です。",
        f"- エネルギーは自分{own_energy}枚、金圏フーディンは"
        f"{min(gold_energy)}〜{max(gold_energy)}枚です。",
        "",
        "### 金メダル圏フーディン（完全リスト）",
        "",
    ]
    for row in rows:
        lines += [
            f"#### #{row['rank']} {row['team_name']} / submission {row['submission_id']}",
            "",
            *deck_lines(row["deck"], cards),
            "",
        ]


def render_report(
    snapshot: str,
    competition: str,
    metadata: dict[str, Any],
    boundaries: dict[str, tuple[int, int]],
    medal_rows: dict[str, list[dict[str, Any]]],
    rival_rows: list[dict[str, Any]],
    own_deck: list[int],
    own_submission: int,
    cards: dict[int, dict[str, str]],
    alakazam_id: int,
) -> str:
    user_rank = metadata["user_rank"]
    lines = [
        "# Kaggle Leaderboard メダル圏・中位層デッキ分析",
        "",
        f"- 取得時刻 (UTC): `{snapshot}`",
        f"- Competition: `{competition}` ({metadata['category']})",
        f"- 参加チーム数: {metadata['team_count']:,}",
        f"- 自チーム現在順位: {user_rank:,}",
        f"- 比較元submission: `{own_submission}`",
        "- デッキ取得方法: 公開リプレイ初期状態の60枚配列",
        "",
        "## 要約",
        "",
    ]
    for medal in ("gold", "silver", "bronze"):
        start, end = boundaries[medal]
        rows = medal_rows[medal]
        lines.append(
            f"- {MEDAL_NAMES[medal]}メダル圏 ({start}–{end}位)の取得{len(rows)}件: "
            f"{_trend_sentence(rows, cards)}"
        )
    rival_rank_min = min((row["rank"] for row in rival_rows), default=user_rank)
    rival_rank_max = max((row["rank"] for row in rival_rows), default=user_rank)
    lines += [
        f"- 中位ライバル層 ({rival_rank_min}–{rival_rank_max}位、自分を除く)の取得"
        f"{len(rival_rows)}件: {_trend_sentence(rival_rows, cards)}",
        "",
        "## メダル圏の定義と調査数",
        "",
        "| 層 | 実際の順位範囲 | 対象チーム数 | 調査数 | 抽出方法 |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for medal in ("gold", "silver", "bronze"):
        start, end = boundaries[medal]
        method = "全件" if len(medal_rows[medal]) == end - start + 1 else "順位帯から均等抽出"
        lines.append(
            f"| {MEDAL_NAMES[medal]} | {start}–{end}位 | {end - start + 1} | "
            f"{len(medal_rows[medal])} | {method} |"
        )

    lines += ["", "## メダル圏の流行デッキ", ""]
    for medal in ("gold", "silver", "bronze"):
        start, end = boundaries[medal]
        rows = medal_rows[medal]
        lines += [
            f"### {MEDAL_NAMES[medal]}メダル圏 ({start}–{end}位)",
            "",
        ]
        _append_trend_table(lines, rows, cards)
        lines += ["", "#### 調査チーム", ""]
        _append_team_table(lines, rows, cards)
        lines.append("")

    lines += [
        "## 中位ライバル層の流行デッキ",
        "",
        f"自チームの{user_rank:,}位を中心に、自分を除く前後のチームを調査します。",
        "",
    ]
    _append_trend_table(lines, rival_rows, cards)
    lines += ["", "### 調査チーム", ""]
    _append_team_table(lines, rival_rows, cards)

    lines += ["", "## 自分のデッキ", "", *deck_lines(own_deck, cards)]
    _append_gold_alakazam_comparison(
        lines, medal_rows["gold"], own_deck, own_submission, cards, alakazam_id
    )
    lines += [
        "## 注意点",
        "",
        "- 開催中のため、メダル境界・順位・Scoreは実行時点の暂定値です。",
        "- 銀・銅は順位帯全体の全数調査ではなく、均等抽出のサンプルです。",
        "- 完全に同じ60枚でも行動選択ロジックによりScoreは異なります。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition", default="pokemon-tcg-ai-battle")
    parser.add_argument("--top", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--tier-sample-size", type=int, default=20)
    parser.add_argument("--rival-radius", type=int, default=10)
    parser.add_argument("--rival-rank", type=int, default=None)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--deck-cache-hours", type=float, default=6.0)
    parser.add_argument("--refresh-decks", action="store_true")
    parser.add_argument("--own-submission", type=int, default=54575814)
    parser.add_argument("--own-deck", type=Path, default=REPO_ROOT / "deck.csv")
    parser.add_argument(
        "--en-cards",
        type=Path,
        default=REPO_ROOT / "data" / "raw" / "EN_Card_Data.csv",
    )
    parser.add_argument(
        "--jp-cards",
        type=Path,
        default=REPO_ROOT / "data" / "raw" / "JP_Card_Data.csv",
    )
    parser.add_argument("--cache-dir", type=Path, default=REPO_ROOT / "data" / "leaderboard_decks")
    parser.add_argument(
        "--report", type=Path, default=REPO_ROOT / "report" / "leaderboard-deck-analysis.md"
    )
    parser.add_argument(
        "--json", type=Path, default=REPO_ROOT / "report" / "leaderboard-deck-analysis.json"
    )
    args = parser.parse_args()
    if (
        args.tier_sample_size <= 0
        or args.rival_radius <= 0
        or args.workers <= 0
        or args.deck_cache_hours < 0
    ):
        parser.error("sample sizes, rival radius, and workers must be positive")

    from kaggle.api.kaggle_api_extended import KaggleApi

    cards = load_cards(args.en_cards, args.jp_cards)
    own_deck = [int(value) for value in args.own_deck.read_text().splitlines() if value.strip()]
    if len(own_deck) != 60:
        raise ValueError(f"Own deck must have 60 cards, got {len(own_deck)}")

    api = KaggleApi()
    api.authenticate()
    metadata = competition_metadata(api, args.competition)
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    api.competition_leaderboard_download(args.competition, str(args.cache_dir), quiet=True)
    entries = parse_leaderboard_zip(args.cache_dir / f"{args.competition}.zip")
    metadata["team_count"] = len(entries)
    if args.rival_rank is not None:
        metadata["user_rank"] = args.rival_rank
    if not metadata["user_rank"]:
        raise RuntimeError("User rank is unavailable; pass --rival-rank explicitly")

    boundaries = medal_boundaries(metadata["team_count"])
    entries_by_rank = {entry.rank: entry for entry in entries}
    segment_ranks: dict[str, list[int]] = {}
    for medal, (start, end) in boundaries.items():
        segment_ranks[medal] = (
            list(range(start, end + 1))
            if medal == "gold"
            else evenly_spaced_ranks(start, end, args.tier_sample_size)
        )
    user_rank = metadata["user_rank"]
    segment_ranks["rivals"] = [
        rank
        for rank in range(
            max(1, user_rank - args.rival_radius),
            min(len(entries), user_rank + args.rival_radius) + 1,
        )
        if rank != user_rank
    ]
    selected_ranks = sorted({rank for ranks in segment_ranks.values() for rank in ranks})

    replay_dir = args.cache_dir / "replays"
    deck_cache_dir = args.cache_dir / "decks"
    replay_dir.mkdir(parents=True, exist_ok=True)
    _seed_deck_cache(args.json, deck_cache_dir, args.deck_cache_hours)
    rows_by_rank: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                fetch_deck_row,
                api,
                entries_by_rank[rank],
                replay_dir,
                deck_cache_dir,
                args.deck_cache_hours,
                args.refresh_decks,
            ): rank
            for rank in selected_ranks
        }
        for completed, future in enumerate(as_completed(futures), 1):
            row = future.result()
            rows_by_rank[row["rank"]] = row
            print(f"[{completed}/{len(futures)}] rank {row['rank']}: {row['team_name']}")

    medal_rows = {
        medal: [rows_by_rank[rank] for rank in ranks]
        for medal, ranks in segment_ranks.items()
        if medal in MEDAL_NAMES
    }
    rival_rows = [rows_by_rank[rank] for rank in segment_ranks["rivals"]]
    snapshot = datetime.now(timezone.utc).isoformat()
    payload = {
        "snapshot_utc": snapshot,
        "competition": args.competition,
        "metadata": metadata,
        "medal_boundaries": {key: list(value) for key, value in boundaries.items()},
        "sampling": {
            "tier_sample_size": args.tier_sample_size,
            "rival_radius": args.rival_radius,
            "rival_rank": user_rank,
            "selected_ranks": segment_ranks,
        },
        "own_submission": args.own_submission,
        "own_deck": own_deck,
        "medal_tiers": medal_rows,
        "rivals": rival_rows,
        # Backward compatibility: the old report's leaderboard was the top 20,
        # which currently matches the complete gold-medal zone.
        "leaderboard": medal_rows["gold"],
        "trends": {
            **{medal: trend_summary(rows, cards) for medal, rows in medal_rows.items()},
            "rivals": trend_summary(rival_rows, cards),
        },
    }
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        render_report(
            snapshot,
            args.competition,
            metadata,
            boundaries,
            medal_rows,
            rival_rows,
            own_deck,
            args.own_submission,
            cards,
            743,
        ),
        encoding="utf-8",
    )
    print(args.report)
    print(args.json)


if __name__ == "__main__":
    main()
