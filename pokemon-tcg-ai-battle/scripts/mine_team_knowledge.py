#!/usr/bin/env python3
"""Mine repository-local Pokemon TCG knowledge across every reachable Git ref.

The miner is intentionally self-contained and deterministic.  It treats Git
objects as evidence, keeps evidence separate from normalized knowledge, and
never checks out or mutates refs/worktrees.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import io
import json
import re
import subprocess
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "artifacts" / "team-knowledge-mining"
MAX_TEXT_BYTES = 8_000_000

OUTPUT_FILES = (
    "branch_inventory.csv",
    "commit_inventory.csv",
    "file_inventory.csv",
    "evidence.jsonl",
    "policy_rules.jsonl",
    "deck_profiles.jsonl",
    "card_combos.jsonl",
    "macros.jsonl",
    "matchup_tips.jsonl",
    "cabt_semantics.jsonl",
    "evaluation_findings.jsonl",
    "failure_modes.jsonl",
    "contradictions.jsonl",
    "coverage.json",
    "summary.json",
    "report.md",
)

ALLOWED_TEXT_SUFFIXES = {
    "",
    ".csv",
    ".json",
    ".jsonl",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

SEARCH_TERMS = (
    "rule", "policy", "strategy", "heuristic", "score", "priority",
    "weight", "bonus", "penalty", "deck", "card", "combo", "matchup",
    "opening", "energy", "bench", "evolve", "retreat", "attack",
    "mulligan", "selection", "option", "legal", "trace", "replay",
    "evaluation", "win", "loss", "fallback", "deterministic", "seed",
    "ルール", "戦略", "優先", "重み", "デッキ", "カード", "コンボ",
    "初動", "展開", "エネルギー", "ベンチ", "進化", "逃げる", "攻撃",
    "勝ち筋", "負け筋", "対策", "評価", "合法手",
)

CATEGORY_PATTERNS: dict[str, tuple[str, ...]] = {
    "GAME_RULE": ("prize", "knock out", "weakness", "resistance", "deck-out", "mulligan", "game rule"),
    "CABT_SEMANTICS": ("cabt", "observation", "selecttype", "selectcontext", "optiontype", "mincount", "maxcount", "option index"),
    "POLICY_RULE": ("policy", "choose_", "_is_useful", "_needs_", "action strategy", "ルール", "戦略", "avoid ", "prefer ", "prioritize"),
    "ACTION_PRIORITY": ("priority", "priorities", "優先", "tie-break", "tie break"),
    "SCORING_HEURISTIC": ("score", "weight", "bonus", "penalty", "heuristic", "threshold", "limit"),
    "DECK_PROFILE": ("deck.csv", "deck list", "deck profile", "archetype", "デッキ"),
    "CARD_COMBO": ("combo", "sequence", "synergy", "rare candy", "jumbo ice cream", "run away draw"),
    "DOMAIN_MACRO": ("setup sequence", "opening plan", "macro", "before using", "then "),
    "MATCHUP_TIP": ("matchup", "crustle", "fighting", "weakness", "対策", "opponent deck"),
    "OPENING_PLAN": ("opening", "setup active", "setup bench", "initial setup", "初動"),
    "RESOURCE_PLAN": ("energy", "deck count", "deck-out", "bench slot", "resource", "prize"),
    "FAILURE_MODE": ("failure", "failed", "bug", "invalid", "illegal", "timeout", "overfit", "regression", "非合法", "失敗"),
    "EVALUATION_FINDING": ("evaluation", "win rate", "wins", "losses", "leaderboard", "score", "benchmark", "評価"),
    "TRACE_OR_DATA_ASSET": ("trace", "jsonl", "fixture", "replay", "artifact", "log"),
    "IMPLEMENTATION_CONTRACT": ("must", "contract", "exactly 60", "unique", "in-range", "schema", "deterministic", "合法"),
}

HIGH_SIGNAL_PATHS = (
    "agents/", "opponents/", "experiments/", "report/", "reports/",
    "data/meta/", "artifacts/knowledge/", "artifacts/search/",
    "docs/evidence/", "docs/competition.md", "docs/kaggle_guide.md",
    "tests/test_rule", "tests/test_first_playable", "tests/test_cabt",
    "tests/test_knowledge", "tests/test_bounded", "tests/test_student",
    "src/mage_ptcg/", "main.py", "deck.csv",
)

USER_IDENTITIES = (
    "onoryosuke", "bfe-lab-ono", "162.chocolate.672@gmail.com",
    "ono.ryosuke.36t@st.kyoto-u.ac.jp",
)
TEAM_IDENTITIES = (
    "niheiryunosuke", "nihei.ryunosuke.38i@st.kyoto-u.ac.jp",
    "zawazawako", "ozawa.kotaro.26d@st.kyoto-u.ac.jp",
)


@dataclass(frozen=True)
class Chunk:
    title: str
    start: int
    end: int
    text: str


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False, errors="replace",
    )
    if check and result.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def stable_json(value: Any, *, pretty: bool = False) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        indent=2 if pretty else None, separators=None if pretty else (",", ":"),
    )


def write_json(path: Path, value: Any) -> None:
    path.write_text(stable_json(value, pretty=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.write_text("".join(stable_json(row) + "\n" for row in rows), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ref_kind(ref: str) -> str:
    if ref.startswith("refs/heads/"):
        return "local_branch"
    if ref.startswith("refs/remotes/"):
        return "remote_branch"
    if ref.startswith("refs/tags/"):
        return "tag"
    return "other"


def short_ref(ref: str) -> str:
    for prefix in ("refs/heads/", "refs/remotes/", "refs/tags/"):
        if ref.startswith(prefix):
            return ref[len(prefix):]
    return ref


def discover_refs() -> list[dict[str, str]]:
    fmt = "%00".join((
        "%(refname)", "%(objectname)", "%(committerdate:iso8601)",
        "%(authorname)", "%(authoremail)", "%(subject)", "%(symref)",
    ))
    raw = git("for-each-ref", "--sort=-committerdate", f"--format={fmt}",
              "refs/heads", "refs/remotes", "refs/tags")
    rows = []
    for line in raw.splitlines():
        parts = line.split("\0")
        if len(parts) != 7:
            continue
        rows.append(dict(zip((
            "ref", "object", "committer_date", "tip_author", "tip_email",
            "subject", "symbolic_target",
        ), parts)))
    return rows


def discover_commits() -> list[dict[str, Any]]:
    fmt = "%x00".join(("%H", "%P", "%an", "%ae", "%aI", "%cn", "%ce", "%cI", "%s"))
    raw = git("log", "--all", "--reverse", f"--format={fmt}")
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        parts = line.split("\0")
        if len(parts) != 9:
            continue
        row = dict(zip((
            "commit", "parents", "author", "author_email", "author_date",
            "committer", "committer_email", "committer_date", "subject",
        ), parts))
        rows.append(row)
    return rows


def discover_worktrees() -> list[dict[str, Any]]:
    blocks = git("worktree", "list", "--porcelain").strip().split("\n\n")
    worktrees: list[dict[str, Any]] = []
    allowed_re = re.compile(r"^(?:\?\?|[ MADRCU?!]{2}) (?:artifacts/team-knowledge-mining/|scripts/mine_team_knowledge.py|tests/test_mine_team_knowledge.py)")
    for block in blocks:
        if not block.strip():
            continue
        data: dict[str, Any] = {"detached": False}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            if key == "worktree":
                data["path"] = value
            elif key == "HEAD":
                data["head"] = value
            elif key == "branch":
                data["branch"] = value
            elif key == "detached":
                data["detached"] = True
        status = subprocess.run(
            ["git", "-C", data["path"], "status", "--short"], text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        lines = [line for line in status.stdout.splitlines() if line and not allowed_re.match(line)]
        data["status"] = lines
        data["readable"] = status.returncode == 0
        local_diff = subprocess.run(
            ["git", "-C", data["path"], "diff", "HEAD", "--"], text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        ).stdout
        data["local_diff_sha256"] = sha256_text(local_diff)
        data["local_diff_knowledge_signal"] = any(
            term.lower() in local_diff.lower() for term in SEARCH_TERMS
        ) and "scripts/orchestration/" not in local_diff
        worktrees.append(data)
    return sorted(worktrees, key=lambda item: item["path"])


def select_base(ref: str, object_id: str, refs: list[dict[str, str]]) -> tuple[str, str]:
    main = next((r for r in refs if r["ref"] == "refs/heads/main"), None)
    belief = next((r for r in refs if r["ref"] == "refs/heads/feature/belief-guided-search"), None)
    if belief and object_id != belief["object"]:
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", belief["object"], object_id],
            cwd=ROOT, check=False,
        ).returncode == 0
        if ancestor:
            return belief["ref"], belief["object"]
    if main and object_id != main["object"]:
        base = git("merge-base", main["object"], object_id).strip()
        return main["ref"], base
    parents = git("show", "-s", "--format=%P", object_id).split()
    return "", parents[0] if parents else object_id


def classify_ownership(commits: list[dict[str, Any]]) -> tuple[str, str, list[str]]:
    identities = sorted({f"{row['author']} <{row['author_email']}>" for row in commits})
    joined = "\n".join(identities).lower()
    has_user = any(token in joined for token in USER_IDENTITIES)
    has_team = any(token in joined for token in TEAM_IDENTITIES)
    if has_user and has_team:
        return "mixed", "high", identities
    if has_user:
        return "likely_user_owned", "high", identities
    if has_team:
        return "likely_team_owned", "high", identities
    if identities:
        return "unknown", "low", identities
    return "unknown", "low", []


def commit_changed_files(commit: str) -> list[tuple[str, str, str]]:
    raw = git("diff-tree", "--root", "--no-commit-id", "--name-status", "-r", "-M", commit)
    changes: list[tuple[str, str, str]] = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if not parts:
            continue
        status = parts[0]
        if status.startswith("R") and len(parts) >= 3:
            changes.append((status, parts[1], parts[2]))
        elif len(parts) >= 2:
            changes.append((status, parts[1], parts[1]))
    return changes


def refs_containing(commit: str, refs: list[dict[str, str]]) -> list[str]:
    output = git("for-each-ref", "--format=%(refname)", "--contains", commit,
                 "refs/heads", "refs/remotes", "refs/tags")
    known = {row["ref"] for row in refs}
    return sorted(ref for ref in output.splitlines() if ref in known)


def build_branch_inventory(
    refs: list[dict[str, str]], commit_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ref in refs:
        base_ref, base_commit = select_base(ref["ref"], ref["object"], refs)
        rev_range = f"{base_commit}..{ref['object']}" if base_commit != ref["object"] else ref["object"]
        range_log_stat = git("log", "--reverse", "--stat", rev_range)
        range_diff_name_status = git("diff", "--name-status", base_commit, ref["object"])
        range_diff = git("diff", base_commit, ref["object"])
        ids = git("rev-list", "--reverse", rev_range).splitlines()
        commit_rows = [commit_by_id[cid] for cid in ids if cid in commit_by_id]
        owner, confidence, authors = classify_ownership(commit_rows)
        changed = set()
        for cid in ids:
            changed.update(new for _status, _old, new in commit_changed_files(cid))
        rows.append({
            **ref,
            "kind": ref_kind(ref["ref"]),
            "short_name": short_ref(ref["ref"]),
            "base_ref": base_ref,
            "base_commit": base_commit,
            "branch_commit_count": len(ids),
            "branch_changed_file_count": len(changed),
            "range_diff_file_count": len([line for line in range_diff_name_status.splitlines() if line]),
            "range_log_stat_sha256": sha256_text(range_log_stat),
            "range_diff_name_status_sha256": sha256_text(range_diff_name_status),
            "range_diff_sha256": sha256_text(range_diff),
            "ownership": owner,
            "ownership_confidence": confidence,
            "ownership_authors": ";".join(authors),
            "inspected": True,
        })
    return rows


def build_commit_inventory(
    commits: list[dict[str, Any]], refs: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, list[tuple[str, str, str]]]]:
    rows: list[dict[str, Any]] = []
    changes_by_commit: dict[str, list[tuple[str, str, str]]] = {}
    for row in commits:
        changes = commit_changed_files(row["commit"])
        changes_by_commit[row["commit"]] = changes
        numstat = git("show", "--format=", "--numstat", row["commit"])
        insertions = deletions = 0
        for line in numstat.splitlines():
            parts = line.split("\t", 2)
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                insertions += int(parts[0])
                deletions += int(parts[1])
        rows.append({
            **row,
            "refs_containing": ";".join(refs_containing(row["commit"], refs)),
            "changed_file_count": len(changes),
            "added_files": sum(status.startswith("A") for status, _old, _new in changes),
            "modified_files": sum(status.startswith("M") for status, _old, _new in changes),
            "deleted_files": sum(status.startswith("D") for status, _old, _new in changes),
            "insertions": insertions,
            "deletions": deletions,
            "show_stat_sha256": sha256_text(git("show", "--stat", "--format=fuller", row["commit"])),
            "show_fuller_sha256": sha256_text(git("show", "--format=fuller", row["commit"])),
            "inspected": True,
        })
    return rows, changes_by_commit


def decode_blob(blob: str) -> tuple[str | None, str]:
    result = subprocess.run(
        ["git", "cat-file", "blob", blob], cwd=ROOT, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        return None, "unreadable_blob"
    data = result.stdout
    if len(data) > MAX_TEXT_BYTES:
        return None, "larger_than_8MB"
    if b"\0" in data:
        return None, "binary"
    try:
        return data.decode("utf-8"), ""
    except UnicodeDecodeError:
        return None, "non_utf8"


def path_is_relevant(path: str) -> bool:
    lower = path.lower()
    if lower.endswith("deck.csv"):
        return True
    return any(lower.startswith(prefix.lower()) or lower == prefix.lower() for prefix in HIGH_SIGNAL_PATHS)


def categorize(text: str, path: str) -> list[str]:
    haystack = f"{path}\n{text}".lower()
    categories = [
        category for category, patterns in CATEGORY_PATTERNS.items()
        if any(pattern.lower() in haystack for pattern in patterns)
    ]
    if not categories and any(term.lower() in haystack for term in SEARCH_TERMS):
        categories = ["OTHER_USEFUL"]
    return categories


def keyword_hits(text: str) -> list[str]:
    lower = text.lower()
    return sorted({term for term in SEARCH_TERMS if term.lower() in lower}, key=str.lower)


def inspect_files(
    commits: list[dict[str, Any]], refs: list[dict[str, str]],
    changes_by_commit: dict[str, list[tuple[str, str, str]]],
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]], dict[str, str]]:
    versions: dict[tuple[str, str], dict[str, Any]] = {}
    blob_text: dict[str, str] = {}
    commit_order = {row["commit"]: index for index, row in enumerate(commits)}
    deleted_paths = {
        old for changes in changes_by_commit.values()
        for status, old, _new in changes if status.startswith("D")
    }
    for commit in commits:
        raw = git("ls-tree", "-r", "-l", commit["commit"])
        for line in raw.splitlines():
            meta, _, path = line.partition("\t")
            bits = meta.split()
            if len(bits) < 4 or bits[1] != "blob":
                continue
            blob = bits[2]
            try:
                size = int(bits[3])
            except ValueError:
                size = -1
            key = (path, blob)
            version = versions.setdefault(key, {
                "path": path, "blob": blob, "size": size, "commits": [],
                "authors": set(), "refs": set(),
            })
            version["commits"].append(commit["commit"])
            version["authors"].add(f"{commit['author']} <{commit['author_email']}>")
    for (path, _blob), version in versions.items():
        for ref in refs:
            if subprocess.run(
                ["git", "merge-base", "--is-ancestor", version["commits"][0], ref["object"]],
                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ).returncode == 0:
                version["refs"].add(ref["ref"])

    inventory: list[dict[str, Any]] = []
    for (path, blob), version in sorted(versions.items()):
        suffix = Path(path).suffix.lower()
        reason = ""
        text: str | None = None
        if suffix not in ALLOWED_TEXT_SUFFIXES:
            reason = "unsupported_extension"
        elif version["size"] > MAX_TEXT_BYTES:
            reason = "larger_than_8MB"
        else:
            if blob not in blob_text:
                decoded, reason = decode_blob(blob)
                if decoded is not None:
                    blob_text[blob] = decoded
            text = blob_text.get(blob)
        hits = keyword_hits(text or "") if text is not None else []
        if text is not None and path.endswith("deck.csv") and text.strip():
            hits = sorted(set(hits) | {"deck"})
        relevant = bool(text is not None and path_is_relevant(path) and hits)
        first_commit = min(version["commits"], key=commit_order.__getitem__)
        last_commit = max(version["commits"], key=commit_order.__getitem__)
        inventory.append({
            "path": path,
            "blob": blob,
            "size_bytes": version["size"],
            "first_commit": first_commit,
            "last_commit": last_commit,
            "commit_occurrences": len(version["commits"]),
            "refs": ";".join(sorted(version["refs"])),
            "authors": ";".join(sorted(version["authors"])),
            "deleted_in_reachable_history": path in deleted_paths,
            "text_inspected": text is not None,
            "knowledge_inspected": relevant,
            "keyword_hits": ";".join(hits),
            "categories": ";".join(categorize(text or "", path)) if relevant else "",
            "skip_reason": reason if text is None else ("no_knowledge_signal" if not relevant else ""),
        })
    return inventory, versions, blob_text


def markdown_chunks(text: str, path: str) -> list[Chunk]:
    lines = text.splitlines()
    headings = [(i, line.lstrip("# ").strip()) for i, line in enumerate(lines) if re.match(r"^#{1,6}\s+", line)]
    if not headings:
        return [Chunk(path, 1, len(lines), text)]
    chunks: list[Chunk] = []
    for pos, (start, title) in enumerate(headings):
        end = headings[pos + 1][0] if pos + 1 < len(headings) else len(lines)
        chunks.append(Chunk(title or path, start + 1, end, "\n".join(lines[start:end])))
    return chunks


def python_chunks(text: str, path: str) -> list[Chunk]:
    lines = text.splitlines()
    chunks: list[Chunk] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [Chunk(path, 1, len(lines), text)]
    prefix_nodes = [node for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))]
    if prefix_nodes:
        start = min(node.lineno for node in prefix_nodes)
        end = min((node.lineno - 1 for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))), default=len(lines))
        if end >= start:
            chunks.append(Chunk("module constants", start, end, "\n".join(lines[start - 1:end])))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            end = getattr(node, "end_lineno", node.lineno)
            chunks.append(Chunk(node.name, node.lineno, end, "\n".join(lines[node.lineno - 1:end])))
    return chunks or [Chunk(path, 1, len(lines), text)]


def text_chunks(text: str, path: str) -> list[Chunk]:
    suffix = Path(path).suffix.lower()
    if suffix == ".md":
        return markdown_chunks(text, path)
    if suffix == ".py":
        return python_chunks(text, path)
    lines = text.splitlines()
    if len(lines) <= 160:
        return [Chunk(path, 1, len(lines), text)]
    return [
        Chunk(f"{path} lines {start + 1}-{min(start + 160, len(lines))}", start + 1,
              min(start + 160, len(lines)), "\n".join(lines[start:start + 160]))
        for start in range(0, len(lines), 160)
    ]


def evidence_type(path: str) -> str:
    if path.startswith("tests/"):
        return "test"
    if path.endswith((".md", ".txt")):
        return "document"
    if path.endswith((".json", ".jsonl", ".csv", ".yaml", ".yml", ".toml")):
        return "artifact" if path.startswith(("artifacts/", "report/", "reports/", "experiments/")) else "config"
    return "code"


def infer_deck_scope(path: str, text: str, refs: list[str]) -> list[str]:
    lower = f"{path}\n{text}\n{' '.join(refs)}".lower()
    scopes = []
    for token, name in (
        ("alakazam", "Alakazam"), ("psychic", "Psychic aggro"),
        ("lucario", "Lucario"), ("crustle", "Crustle"),
        ("abomasnow", "Abomasnow"), ("dragapult", "Dragapult"),
        ("archaludon", "Archaludon"), ("iono", "Iono"),
    ):
        if token in lower:
            scopes.append(name)
    if path.startswith("opponents/"):
        slug = Path(path).parts[1]
        if slug != "official_random":
            scopes.append(slug.replace("_", " "))
    return sorted(set(scopes))


CARD_NAMES = {
    741: "Abra", 742: "Kadabra", 743: "Alakazam", 65: "Dunsparce (legacy)",
    305: "Dunsparce", 66: "Dudunsparce", 140: "Fezandipiti ex", 343: "Shaymin",
    345: "Crustle (Mysterious Rock Inn)", 1079: "Rare Candy",
    1081: "Enhanced Hammer", 1086: "Buddy-Buddy Poffin", 1097: "Night Stretcher",
    1129: "Sacred Ash", 1147: "Jumbo Ice Cream", 1152: "Poke Pad",
    1182: "Boss's Orders", 1184: "Lana's Aid", 1197: "Xerosic's Machinations",
    1225: "Hilda", 1227: "Lillie's Determination", 1231: "Dawn",
    1266: "Nighttime Mine", 13: "Enriching Energy", 19: "Telepath Psychic Energy",
    5: "Basic Psychic Energy",
}


def infer_card_scope(text: str, path: str = "") -> list[str]:
    cards = []
    lower = text.lower()
    for card_id, name in CARD_NAMES.items():
        symbol = re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")
        numeric_deck_entry = path.endswith("deck.csv") and re.search(
            rf"(?m)^\s*{card_id}\s*$", text
        )
        if numeric_deck_entry or name.lower() in lower or symbol in text:
            cards.append(f"{name} [{card_id}]")
    return sorted(set(cards))


def infer_phase(text: str) -> list[str]:
    lower = text.lower()
    phases = []
    for patterns, phase in (
        (("setup", "opening", "initial", "初動"), "opening"),
        (("main", "play", "attach", "evolve", "ability"), "main"),
        (("attack", "knock out"), "attack"),
        (("promotion", "replacement active", "retreat"), "promotion_or_pivot"),
        (("low deck", "deck-out", "critical deck", "終盤"), "late_game"),
    ):
        if any(pattern in lower for pattern in patterns):
            phases.append(phase)
    return phases


def certification(categories: list[str], etype: str, text: str, path: str) -> str:
    lower = text.lower()
    if path.endswith("deck.csv") or (etype == "test" and ("assert" in lower or "pytest" in lower)):
        return "EXACT"
    if path.startswith("experiments/") and "EVALUATION_FINDING" in categories and re.search(r"\d", text):
        return "HEURISTIC"
    if "IMPLEMENTATION_CONTRACT" in categories and any(word in lower for word in ("legal", "mincount", "maxcount", "fallback", "must")):
        return "SOUND_BOUND"
    if any(category in categories for category in ("POLICY_RULE", "ACTION_PRIORITY", "SCORING_HEURISTIC", "MATCHUP_TIP", "EVALUATION_FINDING")) and etype in {"code", "artifact", "log"}:
        return "HEURISTIC"
    if path.startswith("docs/plan/") or etype == "commit":
        return "UNVERIFIED"
    if "CABT_SEMANTICS" in categories and etype == "code":
        return "EXACT"
    return "UNVERIFIED"


def extract_observed_result(text: str) -> str | None:
    metric = re.compile(
        r"\b(?:win|wins|loss|losses|draw|score|rating|fallback|latency|timeout|"
        r"games?|matches?|ci|seed|reward|winner)\b|勝|敗|引き分け|評価|スコア|%",
        re.I,
    )
    lines = [
        line.strip() for line in text.splitlines()
        if re.search(r"\d", line) and metric.search(line)
    ]
    if not lines:
        return None
    return "\n".join(lines[:12])[:1600]


def candidate_uses(categories: list[str], cert: str) -> list[str]:
    uses = set()
    mapping = {
        "GAME_RULE": ("runtime_guard", "knowledge_pack"),
        "CABT_SEMANTICS": ("runtime_guard", "engine_adapter"),
        "POLICY_RULE": ("knowledge_pack", "teacher_feature", "teacher_label", "search_prior"),
        "ACTION_PRIORITY": ("teacher_label", "search_prior"),
        "SCORING_HEURISTIC": ("teacher_feature", "search_prior"),
        "DECK_PROFILE": ("knowledge_pack", "student_training"),
        "CARD_COMBO": ("knowledge_pack", "search_macro"),
        "DOMAIN_MACRO": ("search_macro", "student_training"),
        "MATCHUP_TIP": ("search_prior", "evaluation_only"),
        "EVALUATION_FINDING": ("evaluation_only",),
        "FAILURE_MODE": ("runtime_guard", "evaluation_only"),
        "TRACE_OR_DATA_ASSET": ("student_training", "evaluation_only"),
        "IMPLEMENTATION_CONTRACT": ("runtime_guard", "engine_adapter"),
    }
    for category in categories:
        uses.update(mapping.get(category, ()))
    if cert == "UNVERIFIED":
        uses.add("quarantine")
    return sorted(uses)


def choose_source_ref(refs: list[str]) -> str:
    priorities = (
        "refs/remotes/origin/feature/experiment-a",
        "refs/remotes/origin/feature/deck-psychic-aggro",
        "refs/remotes/origin/feature/meta-opponents",
        "refs/remotes/origin/feature/inspect-options",
        "refs/heads/feature/belief-guided-search",
    )
    for ref in priorities:
        if ref in refs:
            return ref
    return refs[0] if refs else ""


def make_evidence(
    file_inventory: list[dict[str, Any]], versions: dict[tuple[str, str], dict[str, Any]],
    blob_text: dict[str, str], commit_by_id: dict[str, dict[str, Any]],
    changes_by_commit: dict[str, list[tuple[str, str, str]]],
) -> list[dict[str, Any]]:
    drafts: list[dict[str, Any]] = []
    for item in file_inventory:
        if not item["knowledge_inspected"]:
            continue
        path, blob = item["path"], item["blob"]
        text = blob_text[blob]
        version = versions[(path, blob)]
        refs = sorted(version["refs"])
        source_ref = choose_source_ref(refs)
        source_commit = version["commits"][0]
        for chunk in text_chunks(text, path):
            categories = categorize(f"{chunk.title}\n{chunk.text}", path)
            hits = keyword_hits(f"{chunk.title}\n{chunk.text}")
            signal_floor = 1 if path.endswith("deck.csv") or path_is_relevant(path) else 2
            if Path(path).suffix.lower() == ".py" and chunk.title != "module constants" and categories != ["OTHER_USEFUL"]:
                signal_floor = 0
            if path.endswith("deck.csv"):
                categories = ["DECK_PROFILE", "TRACE_OR_DATA_ASSET"]
                hits = ["deck"]
            if path.endswith("/__init__.py") and len(chunk.text.strip()) < 200:
                continue
            if not categories or len(hits) < signal_floor:
                continue
            # Exclude orchestration and generic repository-process material even
            # when it happens to mention policy, score, or fallback.
            if path.startswith(("scripts/orchestration/", "tests/orchestration/", "docs/orchestration/", "docs/agent/ai_orchestrator/")):
                continue
            etype = evidence_type(path)
            cert = certification(categories, etype, chunk.text, path)
            raw = chunk.text.strip()
            if len(raw) > 6000:
                raw = raw[:6000].rstrip() + "\n[…truncated by deterministic 6000-character evidence limit…]"
            first_content = next((
                line.strip(" #\t-*`") for line in chunk.text.splitlines()
                if line.strip() and not line.lstrip().startswith(("import ", "from "))
            ), chunk.title)
            drafts.append({
                "sort_key": (path, blob, chunk.start, chunk.title),
                "categories": categories,
                "title": f"{path}: {chunk.title}",
                "summary": first_content[:500],
                "raw_behavior": raw,
                "ref": source_ref,
                "branch": short_ref(source_ref),
                "commit": source_commit,
                "authors": sorted(version["authors"]),
                "path": path,
                "line_range": f"{chunk.start}-{chunk.end}",
                "evidence_type": etype,
                "deck_scope": infer_deck_scope(path, chunk.text, refs),
                "card_scope": infer_card_scope(chunk.text, path),
                "game_phase": infer_phase(chunk.text),
                "conditions": [],
                "preferred_actions": [],
                "discouraged_actions": [],
                "exceptions": [],
                "constants": {},
                "observed_result": extract_observed_result(chunk.text) if "EVALUATION_FINDING" in categories else None,
                "confidence": "high" if cert in {"EXACT", "SOUND_BOUND"} else ("medium" if cert == "HEURISTIC" else "low"),
                "certification": cert,
                "privacy": "potentially_private" if re.search(r"\b(hidden|private|non-public|非公開)\b", chunk.text, re.I) else ("public_only" if re.search(r"\b(public|observation-only|公開)\b", chunk.text, re.I) else "unknown"),
                "notes": f"Git blob {blob}; this exact file version occurs in {len(version['commits'])} reachable commit snapshot(s).",
            })

    commit_subject_re = re.compile(
        r"alakazam|psychic|deck|strategy|action|agent|cabt|observation|belief|solver|student|"
        r"leaderboard|meta|match|fallback|legal|submission|trace|rule|pokemon|bench|retreat|attack|"
        r"デッキ|戦略|合法|評価|提出|行動", re.I,
    )
    for commit_id, changes in sorted(changes_by_commit.items()):
        row = commit_by_id[commit_id]
        if not commit_subject_re.search(row["subject"]):
            continue
        categories = categorize(row["subject"], "commit")
        if categories == ["OTHER_USEFUL"] and not re.search(r"deck|strategy|cabt|rule|agent|alakazam|psychic|pokemon|デッキ|戦略", row["subject"], re.I):
            continue
        files = sorted({new for _status, _old, new in changes})
        refs = refs_containing(commit_id, discover_refs())
        drafts.append({
            "sort_key": ("~commit", commit_id, 0, row["subject"]),
            "categories": categories,
            "title": f"commit: {row['subject']}",
            "summary": row["subject"],
            "raw_behavior": row["subject"],
            "ref": choose_source_ref(refs),
            "branch": short_ref(choose_source_ref(refs)),
            "commit": commit_id,
            "authors": [f"{row['author']} <{row['author_email']}>"],
            "path": "",
            "line_range": "",
            "evidence_type": "commit",
            "deck_scope": infer_deck_scope("", row["subject"], refs),
            "card_scope": infer_card_scope(row["subject"]),
            "game_phase": infer_phase(row["subject"]),
            "conditions": [], "preferred_actions": [], "discouraged_actions": [],
            "exceptions": [], "constants": {}, "observed_result": None,
            "confidence": "low", "certification": "UNVERIFIED", "privacy": "unknown",
            "notes": "Changed paths: " + ", ".join(files[:30]),
        })

    evidence = []
    for index, draft in enumerate(sorted(drafts, key=lambda item: item.pop("sort_key")), 1):
        evidence.append({"evidence_id": f"EV-{index:06d}", **draft})
    return evidence


def find_evidence(
    evidence: list[dict[str, Any]], *, path: str | None = None,
    title: str | None = None, contains: str | None = None,
) -> list[str]:
    matches = []
    for row in evidence:
        if path is not None and row["path"] != path:
            continue
        if title is not None and title.lower() not in row["title"].lower():
            continue
        if contains is not None and contains.lower() not in row["raw_behavior"].lower():
            continue
        matches.append(row["evidence_id"])
    return matches


def policy_specs(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    specs = [
        ("Rule v0のMAIN行動優先度", "global", [], ["main"], "MAIN selection", "EVOLVE > ATTACH > PLAY > ABILITY > ATTACK > END", [], 600, "EVOLVE=600, ATTACH=500, PLAY=400, ABILITY=300, ATTACK=200, END=-1000", "Rule v0の決定的score順", "HEURISTIC", "agents/rule_agent.py", "module constants", "_MAIN_ACTION_SCORES"),
        ("同点時は小さいoption indexを優先", "cabt_specific", [], [], "score tie", "smallest option index", [], None, None, "候補順序を安定tie-breakとして使う", "HEURISTIC", "agents/rule_agent.py", "_ordered_indices", "index"),
        ("mandatory selectionはminCount件を返す", "cabt_specific", [], [], "mandatory selection", "top-ranked minCount unique indices", [], None, None, "合法な件数境界を守る", "SOUND_BOUND", "agents/rule_agent.py", "choose_rule_indices", "minCount"),
        ("optional non-main selectionは空選択を許す", "cabt_specific", [], [], "non-main and minCount=0", "return []", [], None, None, "不要な対象選択を回避する", "SOUND_BOUND", "agents/rule_agent.py", "rank_rule_indices", "_minimum == 0"),
        ("Alakazam系のsetup active優先", "deck_specific", ["Alakazam"], ["opening"], "setup active", "Dunsparce > Abra > Fezandipiti ex", [], 300, "Dunsparce=300, Abra=200, Fezandipiti ex=100", "初動のActive適性", "HEURISTIC", "main.py", "module constants", "SETUP_ACTIVE_PRIORITY"),
        ("Alakazam系はBasic展開を先行", "deck_specific", ["Alakazam"], ["main"], "Abra/Dunsparce/Fezandipiti exがplay可能", "play setup Basic", [], None, None, "draw/search前に盤面を作る", "HEURISTIC", "main.py", "_choose_main", "Establish Abra"),
        ("Alakazam系は進化後にdraw ability", "deck_specific", ["Alakazam"], ["main"], "evolution available", "evolve before ability", [], None, None, "Kadabra/Alakazamのdrawを先に有効化", "HEURISTIC", "main.py", "_choose_main", "Evolve before"),
        ("低山札ではdeck thinningを抑止", "deck_specific", ["Alakazam"], ["late_game"], "deck count at configured threshold", "skip optional deck searches and thinning", ["Buddy-Buddy Poffin", "Poke Pad"], None, None, "deck-out回避", "HEURISTIC", "main.py", "_choose_main", "DECK_THINNING_ITEMS"),
        ("Crustle対面ではexの無効攻撃を避ける", "matchup_specific", ["Alakazam"], ["main", "attack"], "opponent Active is Crustle 345 and own Active is ex", "retreat to non-ex", ["attack with ex"], None, None, "Mysterious Rock Innによるdamage無効を避ける", "HEURISTIC", "main.py", "_choose_main", "Crustle ID 345"),
        ("Fighting弱点Activeをpivot", "matchup_specific", ["Alakazam"], ["main"], "visible Fighting Active and better Bench matchup", "retreat", ["attach further energy to weak Active"], None, None, "easy KOを避ける", "HEURISTIC", "main.py", "_choose_main", "Fighting-weak"),
        ("Jumbo Ice Creamの使用条件", "deck_specific", ["Alakazam"], ["main"], "Active Alakazam, >=3 energy, >=40 damage", "play Jumbo Ice Cream", ["premature Jumbo Ice Cream"], None, None, "回復効果を無駄にしない", "HEURISTIC", "main.py", "_jumbo_ice_cream_is_useful", "damage >= 40"),
        ("Bossは攻撃可能かつ相手Benchありで優先", "deck_specific", ["Alakazam"], ["main", "attack"], "attack option and opponent Bench", "play Boss's Orders", [], None, None, "gustを攻撃へ接続する", "HEURISTIC", "main.py", "_boss_is_useful", "opponent.bench"),
        ("Xerosicは相手手札4枚以上で使用", "deck_specific", ["Alakazam"], ["main"], "opponent handCount > 3", "play Xerosic's Machinations", [], None, None, "disruptionの対象量を確保", "HEURISTIC", "main.py", "_xerosic_is_useful", "handCount > 3"),
        ("Dudunsparce abilityで最後の後続を消さない", "deck_specific", ["Alakazam"], ["main"], "Run Away Draw candidate", "use only with replacement remaining", ["remove final replacement Pokemon"], None, None, "Active/Bench枯渇を防ぐ", "SOUND_BOUND", "main.py", "_ability_score", "final replacement"),
        ("Experiment Aは最終Bench枠をcore用に予約", "deck_specific", ["Alakazam"], ["opening", "main"], "bench near capacity", "reserve one slot for Abra/Dunsparce", ["fill final slot with Fezandipiti ex"], 1, "RESERVED_BENCH_SLOTS=1", "core setupの詰まりを防ぐ", "HEURISTIC", "main.py", "module constants", "RESERVED_BENCH_SLOTS"),
        ("Experiment Aは後続attackerを維持", "deck_specific", ["Alakazam"], ["main"], "field count <=2 or no ready Bench attacker", "develop successor", [], 2, None, "Active KO後の継続性", "HEURISTIC", "main.py", "_needs_successor", "prepared attacker"),
        ("Experiment AのBoss target順位", "deck_specific", ["Alakazam"], ["attack"], "Bench target can be immediately KO'd", "target win > prizes > energy > stage > max HP > lower HP", [], None, None, "勝利・prize・threatを順に評価", "HEURISTIC", "main.py", "_boss_target_score", "win potential"),
        ("Knowledge priorはRule v0のscore tieだけ変更", "global", [], ["main"], "Knowledge Pack available", "reorder only equal-score actions", ["change order across distinct Rule v0 scores"], None, None, "soft knowledgeで合法候補や基準順位を壊さない", "SOUND_BOUND", "agents/rule_agent_v1.py", "choose", "reorder_ties"),
        ("Rule v1 timeout時はRule v0へfallback", "global", [], [], "elapsed > decision_timeout_ms", "return Rule v0 baseline", [], 25.0, "default decision_timeout_ms=25", "optional belief/knowledge pathの遅延を隔離", "SOUND_BOUND", "agents/rule_agent_v1.py", "choose", "timeout_fallback"),
        ("不正candidateはRule v0へfallback", "global", [], [], "knowledge candidate violates selection bounds", "return Rule v0 baseline", ["return invalid candidate"], None, None, "合法性をhard guardする", "SOUND_BOUND", "agents/rule_agent_v1.py", "choose", "knowledge_invalid_candidate_fallback"),
    ]
    rows = []
    for index, spec in enumerate(specs, 1):
        (name, scope, decks, phases, condition, preferred, discouraged, priority,
         score_effect, rationale, cert, path, title, contains) = spec
        ids = find_evidence(evidence, path=path, title=title, contains=contains)
        if not ids:
            ids = find_evidence(evidence, path=path, contains=contains)
        rows.append({
            "rule_id": f"RULE-{index:06d}", "name": name, "scope": scope,
            "deck_scope": decks, "game_phase": phases,
            "condition": {"description": condition}, "preferred_action": preferred,
            "discouraged_actions": discouraged, "priority": priority,
            "score_effect": score_effect, "exceptions": [], "rationale": rationale,
            "certification": cert, "confidence": "high" if cert == "SOUND_BOUND" else "medium",
            "evidence_ids": ids, "conflicts_with": [],
            "candidate_uses": candidate_uses(["POLICY_RULE"], cert),
        })
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in evidence:
        if (
            item["evidence_type"] == "code"
            and "POLICY_RULE" in item["categories"]
            and (
                item["path"] == "agents/generic_agent.py"
                or item["path"].startswith("opponents/")
            )
            and not item["title"].endswith(": module constants")
        ):
            grouped[(item["path"], item["title"].split(": ", 1)[-1])].append(item)
    for (_path, _title), group in sorted(grouped.items()):
        sample = group[-1]
        index = len(rows) + 1
        decks = sorted({deck for item in group for deck in item["deck_scope"]})
        phases = sorted({phase for item in group for phase in item["game_phase"]})
        cert = "HEURISTIC"
        rows.append({
            "rule_id": f"RULE-{index:06d}",
            "name": sample["title"],
            "scope": "deck_specific" if decks else "global",
            "deck_scope": decks,
            "game_phase": phases,
            "condition": {"source_behavior": sample["summary"]},
            "preferred_action": "Executable ranking/selection behavior in the cited function",
            "discouraged_actions": [],
            "priority": None,
            "score_effect": "See raw code evidence" if "SCORING_HEURISTIC" in sample["categories"] else None,
            "exceptions": [],
            "rationale": "Recovered executable team/opponent policy; strategic optimality is not asserted.",
            "certification": cert,
            "confidence": "medium",
            "evidence_ids": [item["evidence_id"] for item in group],
            "conflicts_with": [],
            "candidate_uses": candidate_uses(["POLICY_RULE"], cert),
        })
    return rows


def parse_deck(text: str) -> list[dict[str, int]] | None:
    try:
        ids = [int(line.strip()) for line in text.splitlines() if line.strip()]
    except ValueError:
        return None
    if not ids:
        return None
    return [{"card_id": card_id, "count": count} for card_id, count in sorted(Counter(ids).items())]


def deck_name(path: str, refs: list[str], commit_subject: str) -> tuple[list[str], list[str]]:
    lower = f"{path} {' '.join(refs)} {commit_subject}".lower()
    aliases: list[str] = []
    subject = commit_subject.lower()
    if path.startswith("tests/fixtures/broken_agents/"):
        return [f"invalid fixture: {Path(path).parent.name.replace('_', ' ')}"], []
    if path.startswith("opponents/"):
        return [Path(path).parent.name.replace("_", " ")], []
    if "psychic" in subject and "aggro" in subject:
        return ["Psychic aggro"], []
    if "alakazam draw engine" in subject:
        return ["Alakazam draw-engine"], []
    if "alakazam control" in subject:
        return ["Alakazam control"], ["Alakazam draw-engine"]
    if "experiment a" in subject or "leaderboard alakazam" in subject:
        return ["Alakazam Experiment A"], ["Leaderboard Alakazam"]
    if "official sample" in subject or "baseline" in subject:
        return ["official sample baseline"], []
    if "first playable" in subject:
        return ["Rule Agent v0 baseline"], []
    if "ruruko_experiment_a" in lower:
        return ["Alakazam Experiment A"], ["Leaderboard Alakazam"]
    if "ruruko_alakazam_control" in lower:
        return ["Alakazam control v2"], ["Alakazam draw-engine"]
    if "alakazam" in lower:
        return ["Alakazam draw-engine"], []
    return [f"historical deck {sha256_text(lower)[:8]}"], aliases


def build_deck_profiles(
    evidence: list[dict[str, Any]], file_inventory: list[dict[str, Any]],
    blob_text: dict[str, str], commit_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    profiles = []
    seen_blobs = set()
    for item in file_inventory:
        if not item["path"].endswith("deck.csv") or item["blob"] in seen_blobs:
            continue
        cards = parse_deck(blob_text.get(item["blob"], ""))
        if cards is None:
            continue
        seen_blobs.add(item["blob"])
        refs = item["refs"].split(";") if item["refs"] else []
        subject = commit_by_id[item["first_commit"]]["subject"]
        names, aliases = deck_name(item["path"], refs, subject)
        ids = [
            row["evidence_id"] for row in evidence
            if row["path"] == item["path"] and f"Git blob {item['blob']}" in row["notes"]
        ]
        key_cards = [
            {"card_id": entry["card_id"], "name": CARD_NAMES.get(entry["card_id"]), "count": entry["count"]}
            for entry in cards if entry["card_id"] in CARD_NAMES and entry["card_id"] not in {3, 5}
        ]
        card_ids = {entry["card_id"] for entry in cards}
        related_ids = list(ids)
        opening_priorities: list[str] = []
        setup_sequence: list[str] = []
        energy_plan: list[str] = []
        bench_plan: list[str] = []
        evolution_plan: list[str] = []
        attack_plan: list[str] = []
        key_combos: list[str] = []
        recovery_plan: list[str] = []
        common_failures: list[str] = []
        win_conditions: list[str] = []
        if item["path"].startswith("opponents/"):
            prefix = str(Path(item["path"]).parent) + "/"
            related_ids.extend(
                row["evidence_id"] for row in evidence
                if row["path"].startswith(prefix) and row["path"].endswith("main.py")
            )
        if {741, 742, 743}.issubset(card_ids):
            related_ids.extend(
                row["evidence_id"] for row in evidence
                if row["path"] in {"main.py", "opponents/ruruko_alakazam_control/main.py", "opponents/ruruko_experiment_a/main.py"}
                and "Alakazam" in row["deck_scope"]
            )
            win_conditions = ["Alakazamを主attackerとし、手札枚数に応じたdamageでKOを取る"]
            opening_priorities = ["Dunsparceをsetup Active候補として優先", "Abraを早期展開"]
            setup_sequence = ["Basic展開", "search/item", "KadabraまたはRare Candy経由でAlakazamへ進化", "draw ability"]
            evolution_plan = ["可能ならability使用前に進化"]
            attack_plan = ["AlakazamをActiveへpivotして攻撃", "Boss's Ordersは即時KOへ接続できる対象を優先"]
            common_failures = ["低山札でdraw/searchを重ねるdeck-out", "Fighting弱点Activeの維持", "Crustleへexで無効攻撃"]
            if 1147 in card_ids:
                energy_plan.append("Active Alakazamへ3 Energyを用意してJumbo Ice Cream条件を満たす")
                key_combos.append("Alakazam + 3 Energy + Jumbo Ice Cream")
            else:
                energy_plan.append("Alakazamの攻撃用Psychic Energyを優先")
            if 13 in card_ids and 66 in card_ids:
                energy_plan.append("Enriching EnergyをDudunsparceへattach")
                key_combos.append("Enriching Energy + Dudunsparce Run Away Draw")
                bench_plan.append("core用に最終Bench枠を1つ予約")
            recovery_cards = [CARD_NAMES[card_id] for card_id in (1097, 1129, 1184) if card_id in card_ids]
            if recovery_cards:
                recovery_plan.append(" / ".join(recovery_cards) + "でPokemon/resourceを回収")
        invalid_fixture = item["path"].startswith("tests/fixtures/broken_agents/")
        profiles.append({
            "sort_key": (names[0], item["blob"]), "names": names, "aliases": aliases,
            "cards": cards, "complete_deck_list": len([x for x in blob_text[item["blob"]].splitlines() if x.strip()]) == 60,
            "key_cards": key_cards, "win_conditions": win_conditions,
            "opening_priorities": opening_priorities,
            "setup_sequence": setup_sequence, "energy_plan": energy_plan,
            "bench_plan": bench_plan, "evolution_plan": evolution_plan,
            "attack_plan": attack_plan, "key_combos": key_combos,
            "recovery_plan": recovery_plan, "common_failures": common_failures,
            "matchups": [],
            "cards_certification": "EXACT",
            "certification": "HEURISTIC" if win_conditions else "EXACT",
            "evidence_ids": sorted(set(related_ids)),
            "candidate_uses": ["evaluation_only"] if invalid_fixture else ["knowledge_pack", "student_training"],
        })
    rows = []
    for index, draft in enumerate(sorted(profiles, key=lambda item: item.pop("sort_key")), 1):
        rows.append({"deck_id": f"DECK-{index:06d}", **draft})
    return rows


def normalized_record(
    prefix: str, index: int, name: str, evidence_ids: list[str], cert: str,
    categories: list[str], **extra: Any,
) -> dict[str, Any]:
    return {
        f"{prefix.lower()}_id": f"{prefix}-{index:06d}", "name": name,
        **extra, "certification": cert, "evidence_ids": evidence_ids,
        "candidate_uses": candidate_uses(categories, cert),
    }


def build_combos(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    specs = [
        ("Alakazam + Jumbo Ice Cream回復", ["Alakazam"], ["Alakazam [743]", "Jumbo Ice Cream [1147]"], ["Active Alakazam", "3 Energy attached", "40以上のdamage"], ["Alakazamへ3 Energyを蓄積", "Jumbo Ice Creamを使用"], "80回復を狙う", [], ["条件未達なら使用しない"], ["低damageまたはEnergy不足"], "_jumbo_ice_cream_is_useful", "damage >= 40"),
        ("Enriching Energy + Dudunsparce draw loop", ["Alakazam"], ["Enriching Energy [13]", "Dudunsparce [66]"], ["Dudunsparce in play", "Enriching Energy attach option"], ["Enriching EnergyをDudunsparceへattach", "Run Away DrawでDudunsparceとEnergyを山札へ戻す"], "反復可能なdraw sequence", [], [], ["Dudunsparce以外へのEnriching Energyは低評価"], "_attachment_score", "strongest repeatable draw sequence"),
        ("進化してからdraw ability", ["Alakazam"], ["Kadabra [742]", "Alakazam [743]"], ["evolution and ability are both available"], ["evolve", "use draw ability"], "進化後のdraw効果を利用", [], [], ["ability先行で進化drawを逃す"], "_choose_main", "Evolve before"),
        ("Boss's Ordersから即時KO", ["Alakazam"], ["Boss's Orders [1182]", "Alakazam [743]"], ["attack available", "opponent Bench target can be KO'd"], ["Boss's Ordersをplay", "有利なBench targetをActiveへ", "attack"], "prizeまたは勝利へ接続", ["Fezandipiti exはBenchを直接攻撃できるためBossを使わない"], [], ["Boss使用でAlakazamのhand-based damageが20低下"], "_boss_target_score", "win potential"),
    ]
    rows = []
    for i, spec in enumerate(specs, 1):
        name, decks, cards, pre, steps, outcome, alt, term, fail, title, contains = spec
        ids = find_evidence(evidence, path="main.py", title=title, contains=contains)
        rows.append({
            "combo_id": f"COMBO-{i:06d}", "name": name, "deck_scope": decks,
            "card_scope": cards, "preconditions": pre, "steps": steps,
            "expected_outcome": outcome, "alternative_steps": alt,
            "termination_conditions": term, "failure_conditions": fail,
            "certification": "HEURISTIC", "evidence_ids": ids,
            "candidate_uses": ["knowledge_pack", "search_macro"],
        })
    return rows


def build_macros(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    specs = [
        ("Alakazam main-phase sequence", ["Alakazam"], ["main"], ["MAIN selection"], ["setup Basicをplay", "有用itemをplay", "evolve", "draw ability", "必要ならretreat", "attach", "supporter", "attack", "END"], ["turn ends"], ["各段階で候補がなければ次へ"], "_choose_main", "Establish Abra"),
        ("安全なcabt minimum fallback", [], [], ["deck固有処理がないselection"], ["0..minCount-1を返す"], ["minCount件を選択"], ["metadata不正時は上位guardが必要"], "_choose_required_minimum", "Safe deterministic fallback"),
        ("Rule v1 guarded selection", [], [], ["registration以外"], ["Rule v0 baseline作成", "public belief更新", "Knowledge Packでtieだけreorder", "timeout/illegalを検査", "candidateまたはbaselineを返す"], ["legal selection returned"], ["timeout・invalid candidate・degraded beliefでRule v0へ戻る"], "choose", "timeout_fallback"),
    ]
    rows = []
    for i, spec in enumerate(specs, 1):
        name, decks, phases, pre, steps, term, fail, title, contains = spec
        ids = find_evidence(evidence, title=title, contains=contains)
        if not ids:
            ids = find_evidence(evidence, contains=contains)
        rows.append({
            "macro_id": f"MACRO-{i:06d}", "name": name, "deck_scope": decks,
            "game_phase": phases, "preconditions": pre, "steps": steps,
            "termination_conditions": term, "failure_conditions": fail,
            "certification": "SOUND_BOUND" if "cabt" in name or "guarded" in name else "HEURISTIC",
            "evidence_ids": ids, "candidate_uses": ["search_macro", "student_training"],
        })
    return rows


def build_matchups(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    specs = [
        ("Alakazam系", "Crustle (Mysterious Rock Inn)", ["Crustle 345"], ["ex Activeならnon-exへretreat", "exで無効攻撃しない"], ["Fezandipiti exのattack継続"], [], ["non-ex Benchが必要"], "Crustle ID 345"),
        ("Alakazam系", "Fighting archetype", ["visible Fighting Active"], ["Fighting弱点でないBenchへpivot"], ["弱点Activeへ追加Energy"], [], ["Dunsparce/Dudunsparce/Fezandipiti exが弱点候補"], "Fighting-weak"),
    ]
    rows = []
    for i, spec in enumerate(specs, 1):
        own, opp, targets, plan, avoid, order, risks, contains = spec
        ids = find_evidence(evidence, path="main.py", contains=contains)
        rows.append({
            "matchup_id": f"MATCHUP-{i:06d}", "own_deck": own,
            "opponent_deck": opp, "priority_targets": targets,
            "recommended_plan": plan, "avoid": avoid, "turn_order_notes": order,
            "known_risks": risks, "observed_results": [],
            "certification": "HEURISTIC", "evidence_ids": ids,
            "candidate_uses": ["evaluation_only", "search_prior"],
        })
    return rows


def category_records(
    evidence: list[dict[str, Any]], category: str, id_prefix: str,
) -> list[dict[str, Any]]:
    selected = [row for row in evidence if category in row["categories"]]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        grouped[(row["path"], row["title"].split(": ", 1)[-1])].append(row)
    rows = []
    for index, (_key, group) in enumerate(sorted(grouped.items()), 1):
        row = group[-1]
        ids = [item["evidence_id"] for item in group]
        certs = {item["certification"] for item in group}
        cert = row["certification"] if len(certs) == 1 else "UNVERIFIED"
        rows.append({
            f"{id_prefix.lower()}_id": f"{id_prefix}-{index:06d}",
            "name": row["title"], "summary": row["summary"],
            "deck_scope": row["deck_scope"], "game_phase": row["game_phase"],
            "observed_result": next(
                (item["observed_result"] for item in reversed(group) if item["observed_result"] is not None),
                None,
            ),
            "certification": cert,
            "confidence": row["confidence"] if cert != "UNVERIFIED" else "low",
            "evidence_ids": ids,
            "candidate_uses": candidate_uses([category], cert),
        })
    return rows


def build_contradictions(evidence: list[dict[str, Any]], rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def ids(path: str, contains: str) -> list[str]:
        return find_evidence(evidence, path=path, contains=contains)
    conflicts = [
        {
            "topic": "MAIN action priority",
            "deck_scope": [], "game_phase": ["main"],
            "claims": [
                {"claim": "inspect-options: ATTACK > ATTACH > EVOLVE > ABILITY > PLAY > RETREAT > END", "evidence_ids": ids("main.py", "OptionType.ATTACK")},
                {"claim": "Rule v0: EVOLVE > ATTACH > PLAY > ABILITY > ATTACK > END", "evidence_ids": ids("agents/rule_agent.py", "_MAIN_ACTION_SCORES")},
            ],
            "newer_claim": "Rule v0 priority", "evaluation_available": True,
            "resolution": "unresolved", "resolution_basis": "Rule v0 is newer and evaluated, but recency alone does not establish strategic correctness.",
        },
        {
            "topic": "initial Bench occupancy",
            "deck_scope": ["Alakazam"], "game_phase": ["opening"],
            "claims": [
                {"claim": "Alakazam control v2 setup selects up to maxCount Bench Pokemon", "evidence_ids": ids("main.py", "count = obs.select.maxCount")},
                {"claim": "Experiment A reserves one final Bench slot for Abra/Dunsparce", "evidence_ids": ids("main.py", "RESERVED_BENCH_SLOTS")},
            ],
            "newer_claim": "reserve one Bench slot", "evaluation_available": True,
            "resolution": "unresolved", "resolution_basis": "The policies target different deck revisions and evaluation records do not isolate this rule.",
        },
        {
            "topic": "low-deck threshold",
            "deck_scope": ["Alakazam"], "game_phase": ["late_game"],
            "claims": [
                {"claim": "Alakazam control v2 uses LOW_DECK_THRESHOLD=10", "evidence_ids": ids("main.py", "LOW_DECK_THRESHOLD = 10")},
                {"claim": "Experiment A uses soft/hard/critical thresholds 18/12/6", "evidence_ids": ids("main.py", "SOFT_DECK_LIMIT")},
            ],
            "newer_claim": "18/12/6 tiered thresholds", "evaluation_available": True,
            "resolution": "unresolved", "resolution_basis": "Thresholds apply to different actions and deck revisions; no isolated comparison proves one set.",
        },
    ]
    rows = []
    for index, row in enumerate(conflicts, 1):
        rows.append({"contradiction_id": f"CONFLICT-{index:06d}", **row})
    rule_by_name = {row["name"]: row for row in rules}
    if "Rule v0のMAIN行動優先度" in rule_by_name:
        rule_by_name["Rule v0のMAIN行動優先度"]["conflicts_with"] = ["CONFLICT-000001"]
    return rows


def evidence_counts_by_branch(evidence: list[dict[str, Any]]) -> Counter[str]:
    return Counter(row["branch"] or "(commit-only/no-ref)" for row in evidence)


def build_report(
    refs: list[dict[str, Any]], worktrees: list[dict[str, Any]],
    commits: list[dict[str, Any]], file_inventory: list[dict[str, Any]],
    evidence: list[dict[str, Any]], rules: list[dict[str, Any]],
    decks: list[dict[str, Any]], combos: list[dict[str, Any]],
    macros: list[dict[str, Any]], matchups: list[dict[str, Any]],
    cabt: list[dict[str, Any]], evaluations: list[dict[str, Any]],
    failures: list[dict[str, Any]], contradictions: list[dict[str, Any]],
    skipped_files: list[str],
) -> str:
    branch_counts = evidence_counts_by_branch(evidence)
    path_counts = Counter(row["path"] for row in evidence if row["path"])
    commit_counts = Counter(row["commit"] for row in evidence if row["commit"])
    commit_subjects = {row["commit"]: row["subject"] for row in commits}
    lines = [
        "# Team Knowledge Mining Report", "",
        "## 1. 調査範囲", "",
        f"到達可能な {len(refs)} ref、{len(worktrees)} local worktree、{len(commits)} unique commitを読取専用Git操作で横断した。歴史的snapshotのfile/blob組を {len(file_inventory)} 件在庫化し、削除済みパスも含めた。外部のPokémon TCG知識は追加していない。", "",
        "## 2. branch・worktree一覧", "",
        "| ref | kind | tip | ownership | confidence | base | commits |", "|---|---|---|---|---|---|---:|",
    ]
    for ref in refs:
        lines.append(f"| `{ref['short_name']}` | {ref['kind']} | `{ref['object'][:12]}` | {ref['ownership']} | {ref['ownership_confidence']} | `{short_ref(ref['base_ref'])}` | {ref['branch_commit_count']} |")
    lines += ["", "### Worktrees", "", "| path | branch | HEAD | local status outside allowed output |", "|---|---|---|---|"]
    for wt in worktrees:
        status = "; ".join(wt["status"]) or "clean"
        lines.append(f"| `{wt['path']}` | `{short_ref(wt.get('branch', 'detached'))}` | `{wt.get('head', '')[:12]}` | {status} |")
    lines += ["", "## 3. 知識が多く見つかったbranch", ""]
    for branch, count in branch_counts.most_common(12):
        lines.append(f"- `{branch}`: {count} evidence")
    lines += ["", "### 高価値commit", ""]
    for commit, count in commit_counts.most_common(10):
        lines.append(f"- `{commit[:12]}`: {count} evidence — {commit_subjects.get(commit, '')}")
    lines += ["", "### 高価値file", ""]
    for path, count in path_counts.most_common(15):
        categories = Counter(
            category for row in evidence if row["path"] == path
            for category in row["categories"]
        )
        kinds = ", ".join(name for name, _count in categories.most_common(4))
        lines.append(f"- `{path}`: {count} evidence（{kinds}）")
    lines += ["", "## 4. 主要なルール群", ""]
    for row in rules:
        lines.append(f"- {row['name']}（{row['certification']}、evidence {len(row['evidence_ids'])}件）")
    lines += ["", "## 5. 主要デッキ", ""]
    for row in decks:
        total = sum(card["count"] for card in row["cards"])
        lines.append(f"- {row['names'][0]}: 確認済み {total} 枚、{len(row['cards'])} card ID（{row['certification']}）")
    lines += ["", "## 6. 主要コンボ", ""]
    lines.extend(f"- {row['name']}" for row in combos)
    lines += ["", "## 7. 主要マクロ", ""]
    lines.extend(f"- {row['name']}" for row in macros)
    lines += ["", "## 8. マッチアップ情報", ""]
    lines.extend(f"- {row['own_deck']} vs {row['opponent_deck']}: {'; '.join(row['recommended_plan'])}" for row in matchups)
    lines += ["", "## 9. cabt固有知識", "", f"cabt/selection/observationに関する正規化記録は `cabt_semantics.jsonl` に {len(cabt)} 件ある。コード契約とテスト観測を区別し、privacy不明の証拠は自動的にpublic扱いしていない。", "",
              "## 10. 評価結果・失敗知識", "", f"評価finding {len(evaluations)} 件、failure mode {len(failures)} 件を抽出した。commit message単独の主張はUNVERIFIEDとして隔離した。", "",
              "## 11. 矛盾", ""]
    for row in contradictions:
        lines.append(f"- {row['contradiction_id']}: {row['topic']}（{row['resolution']}）")
    lines += ["", "## 12. 未調査範囲", ""]
    if skipped_files:
        lines.append(f"binary、非UTF-8、8MB超、対象外拡張子など {len(skipped_files)} historical file versionを内容抽出から除外した。具体的なpath/blobと理由は `file_inventory.csv` および `coverage.json` に記録した。")
    else:
        lines.append("取得不能なrefはなく、skip対象のhistorical file versionもなかった。")
    lines += ["", "品質監査、正典HEADの合否、full test suite、外部一般知識の追加は依頼どおり実施していない。", "",
              "## 13. 各JSONLの用途", "",
              "- `evidence.jsonl`: 原文証拠。正規化前のsource of truth。",
              "- `policy_rules.jsonl`: teacher label、search prior、runtime guard候補。",
              "- `deck_profiles.jsonl`: 確認済みカード構成とdeck scope。",
              "- `card_combos.jsonl` / `macros.jsonl`: 複数手順のsearch macro候補。",
              "- `matchup_tips.jsonl`: matchup別priorと評価scenario候補。",
              "- `cabt_semantics.jsonl`: engine adapterとruntime guard候補。",
              "- `evaluation_findings.jsonl` / `failure_modes.jsonl`: evaluation-only資料と回帰条件。",
              "- `contradictions.jsonl`: 未解決主張のquarantine入口。", ""]
    return "\n".join(lines)


def validate_outputs(output: Path) -> dict[str, Any]:
    jsonl_names = [name for name in OUTPUT_FILES if name.endswith(".jsonl")]
    parsed: dict[str, list[dict[str, Any]]] = {}
    jsonl_valid = True
    try:
        for name in jsonl_names:
            parsed[name] = [json.loads(line) for line in (output / name).read_text(encoding="utf-8").splitlines() if line]
    except (json.JSONDecodeError, OSError):
        jsonl_valid = False
    required = {
        "evidence.jsonl": {"evidence_id", "categories", "title", "summary", "raw_behavior", "ref", "branch", "commit", "authors", "path", "line_range", "evidence_type", "deck_scope", "card_scope", "game_phase", "conditions", "preferred_actions", "discouraged_actions", "exceptions", "constants", "observed_result", "confidence", "certification", "privacy", "notes"},
        "policy_rules.jsonl": {"rule_id", "name", "scope", "deck_scope", "game_phase", "condition", "preferred_action", "discouraged_actions", "priority", "score_effect", "exceptions", "rationale", "certification", "confidence", "evidence_ids", "conflicts_with", "candidate_uses"},
        "deck_profiles.jsonl": {"deck_id", "names", "aliases", "cards", "key_cards", "win_conditions", "opening_priorities", "setup_sequence", "energy_plan", "bench_plan", "evolution_plan", "attack_plan", "key_combos", "recovery_plan", "common_failures", "matchups", "certification", "evidence_ids", "candidate_uses"},
        "card_combos.jsonl": {"combo_id", "name", "deck_scope", "card_scope", "preconditions", "steps", "expected_outcome", "alternative_steps", "termination_conditions", "failure_conditions", "certification", "evidence_ids", "candidate_uses"},
        "macros.jsonl": {"macro_id", "name", "deck_scope", "game_phase", "preconditions", "steps", "termination_conditions", "failure_conditions", "certification", "evidence_ids", "candidate_uses"},
        "matchup_tips.jsonl": {"matchup_id", "own_deck", "opponent_deck", "priority_targets", "recommended_plan", "avoid", "turn_order_notes", "known_risks", "observed_results", "certification", "evidence_ids", "candidate_uses"},
        "contradictions.jsonl": {"contradiction_id", "topic", "deck_scope", "game_phase", "claims", "newer_claim", "evaluation_available", "resolution", "resolution_basis"},
    }
    schema_valid = jsonl_valid and all(
        all(fields.issubset(row) for row in parsed.get(name, []))
        for name, fields in required.items()
    )
    all_ids = []
    for rows in parsed.values():
        for row in rows:
            all_ids.extend(value for key, value in row.items() if key.endswith("_id") and isinstance(value, str))
    ids_unique = len(all_ids) == len(set(all_ids))
    evidence_ids = {row["evidence_id"] for row in parsed.get("evidence.jsonl", [])}
    referenced = set()
    for name, rows in parsed.items():
        if name == "evidence.jsonl":
            continue
        for row in rows:
            referenced.update(row.get("evidence_ids", []))
            for claim in row.get("claims", []):
                referenced.update(claim.get("evidence_ids", []))
    refs_valid = referenced.issubset(evidence_ids) and all(
        row.get("evidence_ids") for name, rows in parsed.items()
        if name not in {"evidence.jsonl", "contradictions.jsonl"}
        for row in rows
    )
    return {
        "jsonl_valid": jsonl_valid,
        "schema_valid": schema_valid,
        "ids_unique": ids_unique,
        "evidence_references_valid": refs_valid,
    }


def mine(output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    refs = discover_refs()
    commits = discover_commits()
    commit_by_id = {row["commit"]: row for row in commits}
    worktrees = discover_worktrees()
    branch_inventory = build_branch_inventory(refs, commit_by_id)
    commit_inventory, changes_by_commit = build_commit_inventory(commits, refs)
    file_inventory, versions, blob_text = inspect_files(commits, refs, changes_by_commit)
    evidence = make_evidence(file_inventory, versions, blob_text, commit_by_id, changes_by_commit)
    rules = policy_specs(evidence)
    decks = build_deck_profiles(evidence, file_inventory, blob_text, commit_by_id)
    combos = build_combos(evidence)
    macros = build_macros(evidence)
    matchups = build_matchups(evidence)
    cabt = category_records(evidence, "CABT_SEMANTICS", "CABT")
    evaluations = category_records(evidence, "EVALUATION_FINDING", "EVAL")
    failures = category_records(evidence, "FAILURE_MODE", "FAIL")
    contradictions = build_contradictions(evidence, rules)

    write_csv(output / "branch_inventory.csv", branch_inventory, list(branch_inventory[0]))
    write_csv(output / "commit_inventory.csv", commit_inventory, list(commit_inventory[0]))
    write_csv(output / "file_inventory.csv", file_inventory, list(file_inventory[0]))
    write_jsonl(output / "evidence.jsonl", evidence)
    write_jsonl(output / "policy_rules.jsonl", rules)
    write_jsonl(output / "deck_profiles.jsonl", decks)
    write_jsonl(output / "card_combos.jsonl", combos)
    write_jsonl(output / "macros.jsonl", macros)
    write_jsonl(output / "matchup_tips.jsonl", matchups)
    write_jsonl(output / "cabt_semantics.jsonl", cabt)
    write_jsonl(output / "evaluation_findings.jsonl", evaluations)
    write_jsonl(output / "failure_modes.jsonl", failures)
    write_jsonl(output / "contradictions.jsonl", contradictions)

    unreadable_refs: list[str] = []
    skipped = [
        f"{row['path']}@{row['blob']}"
        for row in file_inventory if not row["text_inspected"]
    ]
    skip_reasons = Counter(row["skip_reason"] for row in file_inventory if row["skip_reason"])
    relevant_commits = {
        row["first_commit"] for row in file_inventory if row["knowledge_inspected"]
    }
    useful_branches = []
    for branch in branch_inventory:
        base = branch["base_commit"]
        rev_range = f"{base}..{branch['object']}" if base != branch["object"] else branch["object"]
        branch_commits = set(git("rev-list", rev_range).splitlines())
        if branch_commits & relevant_commits:
            useful_branches.append(branch["short_name"])
    useful_branches = sorted(set(useful_branches))
    all_branches = sorted(short_ref(row["ref"]) for row in refs)
    coverage = {
        "refs_discovered": len(refs), "refs_inspected": len(refs),
        "worktrees_discovered": len(worktrees), "worktrees_inspected": sum(wt["readable"] for wt in worktrees),
        "commits_discovered": len(commits), "unique_commits_inspected": len(commits),
        "files_considered": len(file_inventory),
        "files_inspected": sum(row["text_inspected"] for row in file_inventory),
        "knowledge_files_inspected": sum(row["knowledge_inspected"] for row in file_inventory),
        "unreadable_refs": unreadable_refs, "skipped_files": skipped,
        "skip_reasons": dict(sorted(skip_reasons.items())),
        "branches_without_useful_knowledge": sorted(set(all_branches) - set(useful_branches)),
        "branches_with_useful_knowledge": useful_branches,
    }
    write_json(output / "coverage.json", coverage)
    extracted = {
        "evidence": len(evidence), "policy_rules": len(rules),
        "deck_profiles": len(decks), "card_combos": len(combos),
        "macros": len(macros), "matchup_tips": len(matchups),
        "cabt_semantics": len(cabt), "evaluation_findings": len(evaluations),
        "failure_modes": len(failures), "contradictions": len(contradictions),
    }
    head = git("rev-parse", "HEAD").strip()
    current_worktree = next(
        (wt for wt in worktrees if Path(wt["path"]).resolve() == ROOT.resolve()),
        None,
    )
    summary = {
        "status": "READY", "task": "team_knowledge_mining",
        "refs_discovered": len(refs), "refs_inspected": len(refs),
        "worktrees_discovered": len(worktrees), "worktrees_inspected": sum(wt["readable"] for wt in worktrees),
        "unique_commits_inspected": len(commits),
        "files_inspected": coverage["files_inspected"], "extracted": extracted,
        "unreadable_refs": unreadable_refs,
        "generated_files": [f"artifacts/team-knowledge-mining/{name}" for name in OUTPUT_FILES],
        "validation": {
            "jsonl_valid": False, "schema_valid": False, "ids_unique": False,
            "evidence_references_valid": False, "deterministic_output": False,
        },
        "branch": git("branch", "--show-current").strip(), "head": head,
        "tracked_files_modified_outside_allowed_scope": bool(
            current_worktree and current_worktree["status"]
        ),
    }
    write_json(output / "summary.json", summary)
    (output / "report.md").write_text(build_report(
        branch_inventory, worktrees, commits, file_inventory, evidence, rules,
        decks, combos, macros, matchups, cabt, evaluations, failures,
        contradictions, skipped,
    ), encoding="utf-8")
    validation = validate_outputs(output)
    summary["validation"].update(validation)
    write_json(output / "summary.json", summary)
    return summary


def directory_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for name in OUTPUT_FILES:
        if name == "summary.json":
            # deterministic_output is updated after the two-run comparison.
            data = json.loads((path / name).read_text(encoding="utf-8"))
            data["validation"]["deterministic_output"] = False
            payload = stable_json(data, pretty=True).encode("utf-8")
        else:
            payload = (path / name).read_bytes()
        digest.update(name.encode("utf-8") + b"\0" + payload + b"\0")
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--skip-determinism", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.validate_only:
        result = validate_outputs(output)
        print(stable_json(result, pretty=True))
        return 0 if all(result.values()) else 1
    summary = mine(output)
    if not args.skip_determinism:
        with tempfile.TemporaryDirectory(prefix="team-knowledge-mining-") as tmp:
            comparison = Path(tmp) / "output"
            mine(comparison)
            summary["validation"]["deterministic_output"] = (
                directory_digest(output) == directory_digest(comparison)
            )
        write_json(output / "summary.json", summary)
    print(stable_json(summary, pretty=True))
    return 0 if all(summary["validation"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
