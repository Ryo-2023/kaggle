#!/usr/bin/env python3
"""V15 bootstrap に Rule 補完と履歴モデル対戦を追加して固定 Replay を作る。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_ROOT = ROOT / "runs" / "continuous-league-external-v1"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return payload


def _single(pattern: str) -> Path:
    paths = sorted(ROOT.glob(pattern))
    if len(paths) != 1:
        raise RuntimeError(
            f"expected exactly one path for {pattern!r}, found {len(paths)}"
        )
    return paths[0]


def _latest_runtime(stream_root: Path) -> Path:
    events = [_load(path) for path in (stream_root / "events").glob("*.json")]
    if not events:
        raise RuntimeError(f"RuntimePolicy event is missing: {stream_root}")
    event = max(events, key=lambda item: int(item["training_step"]))
    runtime_path = stream_root / "runtime_policies" / event["runtime_policy_id"]
    if not (runtime_path / "manifest.json").is_file():
        raise RuntimeError(f"RuntimePolicy is incomplete: {runtime_path}")
    return runtime_path


def _run_collect(
    *,
    name: str,
    runtime: Path,
    catalog: Path,
    mixture: Path,
    population_epoch_id: str,
    deck: Path,
    episodes: int,
    seed: int,
    output_root: Path,
) -> dict[str, Any]:
    destination = output_root / name
    print(f"[stage] {name}: {episodes} games", flush=True)
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "continuous_league.py"),
            "collect",
            "--runtime",
            str(runtime),
            "--catalog",
            str(catalog),
            "--mixture",
            str(mixture),
            "--population-epoch-id",
            population_epoch_id,
            "--subject-deck-id",
            "v15-current-deck",
            "--deck",
            str(deck),
            "--episodes",
            str(episodes),
            "--seed",
            str(seed),
            "--output",
            str(destination),
        ],
        cwd=ROOT,
        check=True,
    )
    manifest = _load(destination / "chunks" / "collection_manifest.json")
    if (
        manifest.get("status") != "COMPLETE"
        or int(manifest.get("games", -1)) != episodes
        or int(manifest.get("sequences", 0)) <= 0
    ):
        raise RuntimeError(f"collection did not complete: {destination}")
    chunk_manifest = Path(manifest["manifest_path"])
    if not chunk_manifest.is_absolute():
        chunk_manifest = ROOT / chunk_manifest
    if not chunk_manifest.is_file():
        raise RuntimeError(f"experience chunk is missing: {chunk_manifest}")
    return {**manifest, "chunk_manifest": str(chunk_manifest)}


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_RUN_ROOT / "bootstrap-v2" / "collection",
    )
    parser.add_argument("--rule-v0-games", type=int, default=1_000)
    parser.add_argument("--rule-v1-games", type=int, default=500)
    parser.add_argument("--history-games", type=int, default=5_000)
    parser.add_argument("--keep-intermediate", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    for name in ("rule_v0_games", "rule_v1_games", "history_games"):
        if getattr(args, name) <= 0:
            raise ValueError(f"{name} must be positive")

    from mage_ptcg.continuous_league.contracts import atomic_write_json
    from mage_ptcg.continuous_league.replay_sealer import (
        load_sealed_replay,
        seal_replay_dataset,
    )

    output_root = args.output.resolve()
    summary_path = output_root / "collection_summary.json"
    if summary_path.is_file():
        summary = _load(summary_path)
        manifest_path = Path(summary["final_replay_manifest"])
        replay = load_sealed_replay(manifest_path)
        if len(replay) != int(summary["final_sequence_count"]):
            raise RuntimeError("existing final Replay count differs from summary")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    catalog = DEFAULT_RUN_ROOT / "bootstrap-v2" / "catalog_snapshot.json"
    population_path = (
        DEFAULT_RUN_ROOT / "bootstrap-v2" / "population" / "population_epoch.json"
    )
    population_epoch_id = _load(population_path)["population_epoch_id"]
    runtime = _latest_runtime(DEFAULT_RUN_ROOT / "v15-history-stream")
    deck = ROOT / "deck.csv"
    mixtures = {
        "rule-v0": DEFAULT_RUN_ROOT
        / "rule-v0-supplement-population"
        / "mixture.json",
        "rule-v1": DEFAULT_RUN_ROOT
        / "rule-v1-supplement-population"
        / "mixture.json",
        "history": DEFAULT_RUN_ROOT
        / "v15-history-population"
        / "mixture.json",
    }
    parent_manifest = _single(
        "runs/continuous-league-external-v1/imported-v15/replays/*/manifest.json"
    )
    specifications = (
        ("rule-v0", mixtures["rule-v0"], args.rule_v0_games, 82_000),
        ("rule-v1", mixtures["rule-v1"], args.rule_v1_games, 84_000),
        ("history", mixtures["history"], args.history_games, 86_000),
    )
    collections = [
        _run_collect(
            name=name,
            runtime=runtime,
            catalog=catalog,
            mixture=mixture,
            population_epoch_id=population_epoch_id,
            deck=deck,
            episodes=games,
            seed=seed,
            output_root=output_root,
        )
        for name, mixture, games, seed in specifications
    ]

    print("[stage] seal: V15 + Rule v0/v1 + history", flush=True)
    version = seal_replay_dataset(
        chunk_manifests=(
            Path(collection["chunk_manifest"]) for collection in collections
        ),
        output_root=output_root / "replays",
        population_epoch_id=population_epoch_id,
        parent_replay_manifest=parent_manifest,
    )
    verified = load_sealed_replay(version.manifest_path)
    if len(verified) != version.sequence_count:
        raise RuntimeError("sealed Replay verification count mismatch")
    summary = {
        "schema_version": 1,
        "status": "COMPLETE",
        "population_epoch_id": population_epoch_id,
        "candidate_runtime_policy_id": runtime.name,
        "parent_replay_manifest": str(parent_manifest),
        "collections": [
            {
                "name": name,
                "games": collection["games"],
                "sequences": collection["sequences"],
                "outcomes": collection["outcomes"],
            }
            for (name, _mixture, _games, _seed), collection in zip(
                specifications, collections, strict=True
            )
        ],
        "final_replay_dataset_version_id": version.replay_dataset_version_id,
        "final_replay_manifest": str(version.manifest_path),
        "final_sequence_count": version.sequence_count,
        "replay_sha256": version.replay_sha256,
    }
    atomic_write_json(summary_path, summary)
    if not args.keep_intermediate:
        for name, _mixture, _games, _seed in specifications:
            collection_root = output_root / name
            if collection_root.is_dir():
                shutil.rmtree(collection_root)
    (output_root / "runner.pid").unlink(missing_ok=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
