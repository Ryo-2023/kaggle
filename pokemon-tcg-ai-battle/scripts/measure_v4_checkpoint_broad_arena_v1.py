#!/usr/bin/env python3
"""Research-only broad-arena adapter for a closed V4 checkpoint.

The existing V4 held-out evaluator is the single execution authority.  This
thin wrapper only replaces its six-opponent constant with the pre-registered
24-ID broad reference config, then annotates the resulting JSON with the
wrapper/config provenance.  It never changes ``main.py``, the actor pool, or
submission eligibility.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Sequence

_ROOT = Path(__file__).resolve().parents[1]
_CONFIG = _ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"
BROAD_ARENA_SCHEMA_V1 = "meta-specialist-v4-broad-arena-checkpoint-strength-v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def broad_opponent_ids_v1() -> tuple[tuple[str, ...], str]:
    """Load and validate the immutable 24-opponent reference list."""
    try:
        payload = json.loads(_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read broad reference config: {_CONFIG}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != "meta-specialist-performance-first-broad-pool-v1":
        raise ValueError("broad reference config schema mismatch")
    if payload.get("promotion_authority") is not False or payload.get("local_eval_only") is not True:
        raise ValueError("broad reference config must remain research-only")
    ids = payload.get("opponent_ids")
    if not isinstance(ids, list) or len(ids) != 24 or any(type(item) is not str or not item for item in ids):
        raise ValueError("broad reference config must contain 24 non-empty opponent IDs")
    if len(set(ids)) != len(ids):
        raise ValueError("broad reference config contains duplicate opponent IDs")
    if "public_archaludon_cinderace_r7" in ids:
        raise ValueError("smoke-false R7 must not enter the broad reference config")
    manifest_sha = payload.get("pool_manifest_sha256")
    if type(manifest_sha) is not str or len(manifest_sha) != 64 or any(c not in "0123456789abcdef" for c in manifest_sha):
        raise ValueError("broad reference config has invalid pool manifest SHA")
    return tuple(ids), _sha256(_CONFIG)


def _wrapper_sha256() -> str:
    return _sha256(Path(__file__).resolve())


def _output_path(argv: Sequence[str]) -> Path:
    try:
        index = list(argv).index("--output")
        return Path(argv[index + 1])
    except (ValueError, IndexError) as exc:
        raise ValueError("broad evaluator requires --output") from exc


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    ids, config_sha = broad_opponent_ids_v1()
    if "--opponent-count" not in args:
        args.extend(["--opponent-count", str(len(ids))])
    # The base evaluator imports the tuple as a module global and uses it for
    # both argument validation and the output fingerprint.  Reuse that path
    # rather than duplicating CABT/runtime code here.
    from scripts import measure_v4_checkpoint_strength as base

    base.EVAL_HELD_OUT_V1 = ids
    output = _output_path(args)
    result = base.main(args)
    if result != 0:
        return result
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["schema_version"] = BROAD_ARENA_SCHEMA_V1
    payload["broad_reference_config_path"] = str(_CONFIG.resolve())
    payload["broad_reference_config_sha256"] = config_sha
    payload["broad_wrapper_script_sha256"] = _wrapper_sha256()
    payload["research_only"] = True
    payload["promotion_authority"] = False
    payload["longrun_allowed"] = False
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "schema_version": payload["schema_version"],
        "output": str(output),
        "requested_games": payload.get("requested_games"),
        "wins": payload.get("wins"),
        "draws": payload.get("draws"),
        "losses": payload.get("losses"),
        "faults": payload.get("faults"),
        "score_rate": payload.get("score_rate"),
        "broad_reference_config_sha256": config_sha,
        "broad_wrapper_script_sha256": payload["broad_wrapper_script_sha256"],
    }, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
