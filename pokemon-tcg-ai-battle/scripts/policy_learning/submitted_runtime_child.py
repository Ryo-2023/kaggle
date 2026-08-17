#!/usr/bin/env python3
"""Standalone JSONL child for a pinned submitted policy.

It intentionally imports no repository package before the submitted module,
so each asset controls its own import namespace inside a disposable process.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--spec", required=True); args = parser.parse_args()
    try:
        value = json.loads(args.spec); root = Path(value["snapshot_root"]).resolve()
        entrypoint = (root / str(value["entrypoint"]).split(":", 1)[0]).resolve(); deck = Path(value["deck_path"]).resolve()
        if not entrypoint.is_relative_to(root) or not deck.is_relative_to(root):
            raise ValueError("runtime path escapes pinned snapshot")
        if sha(entrypoint) != value["policy_hash"] or sha(deck) != value["deck_hash"]:
            raise ValueError("snapshot identity mismatch")
        os.chdir(root); sys.path.insert(0, str(root))
        name = "submitted_" + hashlib.sha256(f"{value['asset_id']}\0{value['source_commit']}".encode()).hexdigest()
        spec = importlib.util.spec_from_file_location(name, entrypoint)
        if spec is None or spec.loader is None: raise ValueError("cannot construct submitted module")
        module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        callable_name = str(value["entrypoint"]).split(":", 1)[1]; agent = getattr(module, callable_name)
        if not callable(agent): raise ValueError("submitted entrypoint is not callable")
        print(json.dumps({"status": "READY", "policy_hash": value["policy_hash"], "deck_hash": value["deck_hash"]}), flush=True)
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "code": "IMPORT_FAILURE", "message": f"{type(exc).__name__}: {str(exc)[:500]}"}), flush=True); return 2
    for line in sys.stdin:
        try:
            action = agent(json.loads(line)["observation"])
            print(json.dumps({"status": "OK", "action": action}, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps({"status": "ERROR", "code": "POLICY_EXCEPTION", "message": f"{type(exc).__name__}: {str(exc)[:400]}"}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
