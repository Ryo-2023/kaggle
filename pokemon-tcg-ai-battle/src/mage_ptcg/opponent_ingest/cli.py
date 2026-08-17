from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from .pipeline import run_ingestion


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mage_ptcg.opponent_ingest")
    parser.add_argument("command", choices=("discover", "fetch", "normalize-decks", "audit-agents", "build-bindings", "classify-families", "build-candidate-population", "validate", "report", "run"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--mode", choices=("incremental", "full"), default="incremental")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    config = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    print(json.dumps(run_ingestion(args.repo.resolve(), args.artifact_root, config, mode=args.mode), ensure_ascii=False, sort_keys=True))
    return 0
