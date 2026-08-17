#!/usr/bin/env python3
"""MAGE-PTCG 正典ドキュメントの構造検証。

docs/plan/design/ と docs/plan/implementation/ の正典文書について、
front matter、必須節、Notionページ対応、secret混入を検査し、
docs/notion/local_hashes.json を再生成する。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CANONICAL_DIRS = (
    ROOT / "docs" / "plan" / "design",
    ROOT / "docs" / "plan" / "implementation",
)
PAGE_MAP = ROOT / "docs" / "notion" / "page_map.yaml"
LOCAL_HASHES = ROOT / "docs" / "notion" / "local_hashes.json"

REQUIRED = {
    "project", "document_status", "canonical_source", "initial_source",
    "initial_sync_date", "language", "notion_page_id", "notion_url", "title",
}

# 完了条件の見出しは既存文書の表記ゆれ（完了条件／完了の定義）を許容する
COMPLETION_PATTERN = re.compile(r"完了条件|完了の定義")


def front_matter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: missing front matter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError(f"{path}: unterminated front matter")
    result = {}
    for line in text[4:end].splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip()
    return result


def page_map_entries() -> dict[str, str]:
    """page_map.yaml から local_path → notion_page_id を素朴に抽出する。"""
    entries: dict[str, str] = {}
    current_path: str | None = None
    for line in PAGE_MAP.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("- local_path:"):
            current_path = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("notion_page_id:") and current_path:
            entries[current_path] = stripped.split(":", 1)[1].strip()
            current_path = None
    return entries


def main() -> int:
    errors: list[str] = []
    page_ids: dict[str, Path] = {}
    paths = sorted(p for d in CANONICAL_DIRS for p in d.glob("*.md"))

    hashes = {}
    for path in paths:
        try:
            meta = front_matter(path)
        except ValueError as exc:
            errors.append(str(exc))
            continue

        missing = REQUIRED - meta.keys()
        if missing:
            errors.append(f"{path}: missing {sorted(missing)}")

        page_id = meta.get("notion_page_id")
        if page_id:
            if page_id in page_ids:
                errors.append(f"duplicate page ID {page_id}")
            page_ids[page_id] = path

        text = path.read_text(encoding="utf-8")
        if "## " not in text or not COMPLETION_PATTERN.search(text):
            errors.append(f"{path}: missing sections or completion conditions")
        if re.search(r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*\S+", text):
            errors.append(f"{path}: possible secret-like value")

        hashes[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()

    if not PAGE_MAP.exists():
        errors.append(f"missing {PAGE_MAP}")
    else:
        mapped = page_map_entries()
        for local_path, page_id in mapped.items():
            file_path = ROOT / local_path
            if not file_path.exists():
                errors.append(f"page_map: {local_path} does not exist")
                continue
            if file_path in page_ids.values():
                meta_id = front_matter(file_path).get("notion_page_id")
                if meta_id != page_id:
                    errors.append(
                        f"page_map: {local_path} front matter ID {meta_id} != map ID {page_id}"
                    )
        for path in paths:
            rel = str(path.relative_to(ROOT))
            if rel not in mapped:
                errors.append(f"page_map: missing entry for {rel}")

    LOCAL_HASHES.write_text(
        json.dumps(hashes, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if errors:
        print("Documentation validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"Validated {len(paths)} canonical documents.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
