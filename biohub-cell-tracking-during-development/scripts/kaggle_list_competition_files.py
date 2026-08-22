"""List (never download) the Kaggle competition file manifest and summarise it.

Answers "how big is the full train set, and what is downloadable?" without
touching a single byte of the data itself. Pure listing via the Kaggle API;
there is no ``download`` call anywhere in this file, by design.

The competition stores each Zarr/GEFF chunk as an individual API file entry, so
the raw listing is tens of thousands of rows and the API rate-limits (HTTP 429)
well before the end. The lister is therefore **resumable**: raw
``{name, bytes}`` rows are appended to a JSONL cache together with the next page
token, and re-running the script continues from where it stopped. Aggregation by
split and sample id is a separate pass over the cache.

Usage (inside the container)::

    python scripts/kaggle_list_competition_files.py \
        --cache artifacts/validation_design/kaggle_files.jsonl \
        --out   artifacts/validation_design/kaggle_manifest_summary.json

Requires ``~/.kaggle/credentials.json`` (or kaggle.json). If credentials are
missing the script reports the exact failing call so the caller can report
BLOCKED instead of guessing numbers.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

# ``train/<sample>.zarr/...`` / ``train/<sample>.geff/...`` / ``test/<sample>.zarr/...``
_ENTRY_RE = re.compile(r"^(?P<split>[^/]+)/(?P<sample>[^/]+)\.(?P<kind>zarr|geff)(?:/(?P<rest>.*))?$")

_STATE_SUFFIX = ".state.json"


def _load_state(cache: Path) -> dict:
    state_path = cache.with_suffix(cache.suffix + _STATE_SUFFIX)
    if state_path.exists():
        return json.loads(state_path.read_text())
    return {"next_page_token": None, "pages": 0, "done": False}


def _save_state(cache: Path, state: dict) -> None:
    cache.with_suffix(cache.suffix + _STATE_SUFFIX).write_text(json.dumps(state) + "\n")


def fetch(
    competition: str,
    cache: Path,
    page_size: int = 200,
    sleep: float = 1.0,
    max_pages: int | None = None,
    max_retries: int = 8,
) -> dict:
    """Page through the listing into *cache* (JSONL). Resumable, throttled."""
    import kaggle

    api = kaggle.api
    cache.parent.mkdir(parents=True, exist_ok=True)
    state = _load_state(cache)
    if state.get("done"):
        print(f"listing already complete ({state['pages']} pages)", file=sys.stderr)
        return state

    token = state.get("next_page_token")
    pages_this_run = 0
    with cache.open("a") as fh:
        while True:
            kwargs: dict[str, Any] = {"page_size": page_size}
            if token:
                kwargs["page_token"] = token

            backoff = sleep
            for attempt in range(max_retries):
                try:
                    response = api.competition_list_files(competition, **kwargs)
                    break
                except Exception as exc:
                    if "429" not in str(exc) or attempt == max_retries - 1:
                        _save_state(cache, state)
                        raise
                    backoff = min(backoff * 2, 120.0)
                    print(
                        f"  429 at page {state['pages'] + 1}; sleeping {backoff:.0f}s",
                        file=sys.stderr,
                        flush=True,
                    )
                    time.sleep(backoff)

            files = list(response.files or [])
            for f in files:
                fh.write(json.dumps([f.name, int(f.total_bytes or 0)]) + "\n")
            fh.flush()

            state["pages"] += 1
            pages_this_run += 1
            token = response.next_page_token
            state["next_page_token"] = token
            if not token or not files:
                state["done"] = True
                _save_state(cache, state)
                print(f"listing complete after {state['pages']} pages", file=sys.stderr)
                return state
            if state["pages"] % 10 == 0:
                _save_state(cache, state)
                print(
                    f"  page {state['pages']:5d} (~{state['pages'] * page_size} entries)",
                    file=sys.stderr,
                    flush=True,
                )
            if max_pages is not None and pages_this_run >= max_pages:
                _save_state(cache, state)
                print(f"stopping after {pages_this_run} pages this run", file=sys.stderr)
                return state
            time.sleep(sleep)


def aggregate(cache: Path, competition: str, complete: bool) -> dict:
    total_files = 0
    total_bytes = 0
    split_files: dict[str, int] = defaultdict(int)
    split_bytes: dict[str, int] = defaultdict(int)
    samples: dict[str, dict[str, dict[str, list[int]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(lambda: [0, 0]))
    )
    loose: list[tuple[str, int]] = []
    seen: set[str] = set()

    with cache.open() as fh:
        for line in fh:
            name, size = json.loads(line)
            if name in seen:  # a resumed run can re-fetch a boundary page
                continue
            seen.add(name)
            total_files += 1
            total_bytes += size
            m = _ENTRY_RE.match(name)
            if m is None:
                head = name.split("/", 1)[0] if "/" in name else "<root>"
                split_files[head] += 1
                split_bytes[head] += size
                if "/" not in name:
                    loose.append((name, size))
                continue
            split, sample, kind = m["split"], m["sample"], m["kind"]
            split_files[split] += 1
            split_bytes[split] += size
            entry = samples[split][sample][kind]
            entry[0] += 1
            entry[1] += size

    out: dict = {
        "competition": competition,
        "listing_only": True,
        "listing_complete": complete,
        "total_files": total_files,
        "total_bytes": total_bytes,
        "total_gib": round(total_bytes / 2**30, 3),
        "loose_files": [{"name": n, "bytes": b} for n, b in sorted(loose)],
        "splits": {},
    }
    for split in sorted(split_files):
        per_sample = samples.get(split, {})
        rows = []
        for sample in sorted(per_sample):
            kinds = per_sample[sample]
            rows.append(
                {
                    "sample_id": sample,
                    "zarr_files": kinds.get("zarr", [0, 0])[0],
                    "zarr_bytes": kinds.get("zarr", [0, 0])[1],
                    "geff_files": kinds.get("geff", [0, 0])[0],
                    "geff_bytes": kinds.get("geff", [0, 0])[1],
                }
            )
        out["splits"][split] = {
            "files": split_files[split],
            "bytes": split_bytes[split],
            "gib": round(split_bytes[split] / 2**30, 3),
            "n_samples": len(rows),
            "n_samples_with_geff": sum(1 for r in rows if r["geff_files"] > 0),
            "n_samples_with_zarr": sum(1 for r in rows if r["zarr_files"] > 0),
            "median_sample_zarr_bytes": (
                sorted(r["zarr_bytes"] for r in rows)[len(rows) // 2] if rows else 0
            ),
            "samples": rows,
        }
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--competition", default="biohub-cell-tracking-during-development")
    p.add_argument("--page-size", type=int, default=200, help="Kaggle API max is 200.")
    p.add_argument("--cache", type=Path, required=True, help="Resumable JSONL listing cache.")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--sleep", type=float, default=1.0, help="Seconds between pages.")
    p.add_argument("--max-pages", type=int, default=None, help="Stop after N pages this run.")
    p.add_argument("--aggregate-only", action="store_true")
    args = p.parse_args()

    state = _load_state(args.cache)
    if not args.aggregate_only:
        try:
            state = fetch(
                args.competition, args.cache, args.page_size, args.sleep, args.max_pages
            )
        except Exception as exc:
            print(
                f"BLOCKED: kaggle competition_list_files({args.competition!r}) failed: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            if not args.cache.exists():
                return 2

    if not args.cache.exists():
        print(f"BLOCKED: no listing cache at {args.cache}", file=sys.stderr)
        return 2

    summary = aggregate(args.cache, args.competition, bool(state.get("done")))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")

    print(f"competition       : {summary['competition']}")
    print(f"listing complete  : {summary['listing_complete']}")
    print(f"total files       : {summary['total_files']}")
    print(f"total bytes       : {summary['total_bytes']} ({summary['total_gib']} GiB)")
    for split, info in summary["splits"].items():
        print(
            f"  {split:<10s} files={info['files']:>7d} "
            f"bytes={info['bytes']:>14d} ({info['gib']:>8.3f} GiB) "
            f"samples={info['n_samples']:>4d} "
            f"(zarr={info['n_samples_with_zarr']}, geff={info['n_samples_with_geff']})"
        )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
