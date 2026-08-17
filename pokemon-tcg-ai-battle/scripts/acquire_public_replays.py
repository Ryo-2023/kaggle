"""Resume-safe, read-only acquisition of public Kaggle competition replays.

The checkpoint stores only request state and hashes.  Replay payloads remain
outside the repository.  Empty responses, rate limits, and authentication
failures are classified rather than silently retried forever.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def classify(stderr: str, stdout: str, returncode: int) -> str:
    text = (stderr + "\n" + stdout).lower()
    if "rate" in text or "429" in text:
        return "RATE_LIMIT"
    if "auth" in text or "credential" in text or "401" in text or "403" in text:
        return "AUTH_FAILURE"
    if not stdout.strip():
        return "EMPTY_RESPONSE"
    return "COMMAND_FAILURE" if returncode else "MALFORMED_RESPONSE"


def run_json(args: list[str], retries: int) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    for attempt in range(1, retries + 1):
        result = subprocess.run(args, text=True, capture_output=True, check=False)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = None
        audit = {"command": args, "attempt": attempt, "returncode": result.returncode, "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(), "stderr_sha256": hashlib.sha256(result.stderr.encode()).hexdigest(), "observed_at": now()}
        if result.returncode == 0 and isinstance(payload, list):
            audit["status"] = "OK"
            return payload, audit
        audit["status"] = classify(result.stderr, result.stdout, result.returncode)
        if audit["status"] in {"AUTH_FAILURE", "EMPTY_RESPONSE"}:
            return None, audit
        if attempt < retries:
            time.sleep(min(2 ** (attempt - 1), 8))
    return None, audit


def submission_ids(path: Path) -> list[str]:
    with path.open(encoding="utf-8") as handle:
        return sorted({str(row["submission_id"]) for row in csv.DictReader(handle) if row.get("submission_id")})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submissions", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--maximum-replays", type=int, default=100)
    parser.add_argument("--per-submission", type=int, default=5)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--retry-rate-limited", action="store_true", help="Retry prior RATE_LIMIT episode listings once in this invocation.")
    parser.add_argument("--max-episode-requests", type=int, default=20, help="Hard cap for episode-list requests in this invocation.")
    args = parser.parse_args()
    if args.maximum_replays < 1 or args.per_submission < 1 or args.retries < 1 or args.max_episode_requests < 1:
        raise ValueError("limits must be positive")
    args.raw_root.mkdir(parents=True, exist_ok=True)
    state = json.loads(args.state.read_text(encoding="utf-8")) if args.state.exists() else {"schema": "public-replay-acquisition-v1", "episodes": {}, "replays": {}, "attempts": []}
    state.setdefault("episodes", {}); state.setdefault("replays", {}); state.setdefault("attempts", [])
    episode_requests = 0
    for submission_id in submission_ids(args.submissions):
        previous = state["episodes"].get(submission_id)
        should_retry = args.retry_rate_limited and isinstance(previous, dict) and previous.get("status") == "RATE_LIMIT"
        if submission_id not in state["episodes"] or should_retry:
            if episode_requests >= args.max_episode_requests:
                break
            rows, audit = run_json(["kaggle", "competitions", "episodes", submission_id, "--format", "json"], args.retries)
            episode_requests += 1
            state["attempts"].append(audit)
            state["episodes"][submission_id] = {"status": audit["status"], "records": rows or []}
            atomic_json(args.state, state)
        records = state["episodes"][submission_id].get("records", [])[:args.per_submission]
        for episode in records:
            episode_id = str(episode.get("id", ""))
            if not episode_id or episode_id in state["replays"] or len(state["replays"]) >= args.maximum_replays:
                continue
            destination = args.raw_root / episode_id
            destination.mkdir(exist_ok=True)
            result = subprocess.run(["kaggle", "competitions", "replay", episode_id, "--path", str(destination), "--quiet"], text=True, capture_output=True, check=False)
            files = sorted(path for path in destination.rglob("*") if path.is_file())
            status = "OK" if result.returncode == 0 and files else classify(result.stderr, result.stdout, result.returncode)
            state["replays"][episode_id] = {"submission_id": submission_id, "status": status, "attempted_at": now(), "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(), "stderr_sha256": hashlib.sha256(result.stderr.encode()).hexdigest(), "files": [{"name": str(path.relative_to(args.raw_root)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in files]}
            atomic_json(args.state, state)
    state["completed_at"] = now()
    atomic_json(args.state, state)
    print(json.dumps({"submissions": len(state["episodes"]), "replays": len(state["replays"]), "ok_replays": sum(row["status"] == "OK" for row in state["replays"].values())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
