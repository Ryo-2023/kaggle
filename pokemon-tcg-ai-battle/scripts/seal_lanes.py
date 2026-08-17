"""複数レーンの teacher corpus を、**1 本のキュー**で封印する。

## なぜレーンごとにプールを分けないか

レーンの大きさは揃わない (実測: archaludon 3.4 GB, grimmsnarl 5.0 GB,
rocket 5.2 GB, alakazam 8.7 GB)。レーンごとにコアを 1/4 ずつ固定すると、小さい
レーンが先に終わってそのコアが遊び、いちばん大きいレーンだけが 1/4 のコアで残りを
処理する。

そこで、全レーンの chunk を 1 つのプールへ入れる。chunk は互いに独立なので、
小さいレーンの chunk が尽きた時点で全 worker が自動的に大きいレーンへ回る。
「終わった worker を途中参加させる」処理を書く必要はなく、そもそも取り合う
キューが 1 本しかない状態にする。

段階間の barrier も置かない。chunk の導出が終わったレーンから順に split を確定し、
shard 生成 job を同じプールへ投入する。他のレーンはその間も導出を続ける。

## 端末表示

AGENTS.md「長時間実験の端末表示」に従い、この supervisor だけが端末を持ち、同じ
数行を上書きする。レーンごとの行ログは出さない。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.actor_pool_v1 import _build_actor_pool_deck_binding_v1
from mage_ptcg.meta_specialist.training_snapshot_v1 import seal_sharded_corpora_v1

from run_parallel_lanes import LANE_PRESETS_V1

DEFAULT_CHUNK_MAX_BYTES = 64 * 1024 * 1024


def _bar(fraction: float, width: int = 24) -> str:
    filled = int(max(0.0, min(1.0, fraction)) * width)
    return "#" * filled + "." * (width - filled)


class Dashboard:
    """Owns the terminal: overwrites the same block, never scrolls it away."""

    def __init__(self, *, is_tty: bool, snapshot_interval: float) -> None:
        self._is_tty = is_tty
        self._height = 0
        self._snapshot_interval = snapshot_interval
        self._last = 0.0

    def draw(self, lines: list[str]) -> None:
        if not self._is_tty:
            now = time.monotonic()
            if now - self._last < self._snapshot_interval:
                return
            self._last = now
            print("\n".join(lines), flush=True)
            return
        out = sys.stdout
        if self._height:
            out.write(f"\033[{self._height}A")
        for line in lines:
            out.write("\033[2K" + line + "\n")
        for _ in range(max(0, self._height - len(lines))):
            out.write("\033[2K\n")
        self._height = max(len(lines), self._height)
        out.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lanes", default="all",
                        help=f"'all' か、カンマ区切り ({', '.join(sorted(LANE_PRESETS_V1))})")
    parser.add_argument("--run-prefix", default="t1")
    parser.add_argument("--collection-base", default="runs/meta-specialist-teacher-records")
    parser.add_argument("--environment-version", default="cabt-local-v1")
    parser.add_argument("--shard-max-examples", type=int, default=20000)
    parser.add_argument(
        "--chunk-max-bytes", type=int, default=DEFAULT_CHUNK_MAX_BYTES,
        help="chunk 1 個の上限 byte 数。小さいほど並列度が上がり、同時に載るメモリは "
             "おおむね workers x この値になる",
    )
    parser.add_argument(
        "--workers", type=int, default=0,
        help="全レーン合計の同時プロセス数。0 でコア数。レーンごとに分割しないので、"
             "先に終わったレーンのコアは残りのレーンへそのまま回る",
    )
    parser.add_argument("--refresh-seconds", type=float, default=1.0)
    parser.add_argument("--snapshot-seconds", type=float, default=30.0)
    parser.add_argument(
        "--force", action="store_true",
        help="snapshot_index.json が既にあるレーンも作り直す。既定では飛ばす",
    )
    args = parser.parse_args()

    names = (
        sorted(LANE_PRESETS_V1) if args.lanes == "all"
        else [x.strip() for x in args.lanes.split(",") if x.strip()]
    )
    unknown = [name for name in names if name not in LANE_PRESETS_V1]
    if unknown:
        raise SystemExit(f"unknown lanes {unknown}; known: {sorted(LANE_PRESETS_V1)}")

    qualification_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    specs = []
    skipped = []
    for name in names:
        preset = LANE_PRESETS_V1[name]
        run_dir = Path(args.collection_base) / f"{args.run_prefix}-{name}"
        if not args.force and (run_dir / "snapshot_index.json").is_file():
            # Sealing a lane costs roughly a quarter of an hour of full-machine
            # work.  Redoing a lane that already has an index -- because another
            # lane failed and the command was rerun -- wastes that for nothing.
            skipped.append(name)
            continue
        collection = json.loads(
            (run_dir / "teacher_dataset_manifest.json").read_text(encoding="utf-8")
        )
        record_files = sorted((run_dir / "records").glob("*.jsonl"))
        if not record_files:
            raise SystemExit(f"{name}: no records under {run_dir / 'records'}")
        # The vocabulary is a capability object the workers rebuild for
        # themselves; the parent needs its own for the shard identity fields.
        _qualified, _lock, vocabulary = _build_actor_pool_deck_binding_v1(
            archetype_id=preset["archetype_id"],
            deck_csv_path=Path(collection["subject_deck_csv_path"]),
            source_commit=collection["source_commit"],
        )
        specs.append({
            "name": name,
            "record_files": record_files,
            "chunk_max_bytes": args.chunk_max_bytes,
            "output_dir": run_dir,
            "archetype_id": preset["archetype_id"],
            "deck_csv_path": collection["subject_deck_csv_path"],
            "source_commit": collection["source_commit"],
            "permission_manifest": collection["permission_manifest"],
            "environment_version": args.environment_version,
            "qualification_time_utc": qualification_time,
            "shard_max_examples": args.shard_max_examples,
            "vocabulary": vocabulary,
        })

    if skipped:
        print(f"[seal-lanes] 封印済みのため skip: {skipped}  "
              f"(作り直すなら --force)", flush=True)
    if not specs:
        print("[seal-lanes] 封印すべきレーンがありません", flush=True)
        return 0

    workers = args.workers or (os.cpu_count() or 1)
    is_tty = sys.stdout.isatty()
    dashboard = Dashboard(is_tty=is_tty, snapshot_interval=args.snapshot_seconds)
    started = time.monotonic()
    state: dict = {"last": 0.0}

    print(f"[seal-lanes] lanes={names} workers={workers} "
          f"chunk_max_bytes={args.chunk_max_bytes} shard_max_examples={args.shard_max_examples}",
          flush=True)
    if is_tty:
        print()

    def on_progress(event: dict) -> None:
        now = time.monotonic()
        if now - state["last"] < args.refresh_seconds and event["chunks_done"] < event["chunks"]:
            return
        state["last"] = now
        elapsed = now - started
        done = event["chunks_done"]
        rate = done / elapsed if elapsed > 0 else 0.0
        remaining = (event["chunks"] - done) / rate if rate > 0 else float("nan")
        lines = [
            f"elapsed={int(elapsed) // 60:d}m{int(elapsed) % 60:02d}s  "
            f"chunks {done}/{event['chunks']}  shards {event['shards_done']}/"
            f"{event['shard_jobs']}  {rate:.2f} chunk/s  "
            f"eta~{'--' if remaining != remaining else f'{int(remaining) // 60}m'}",
            "",
            f"{'lane':<14}{'derive':<28}{'chunks':>10}  {'state':<10}",
        ]
        for lane in event["lanes"]:
            fraction = lane["chunks_done"] / lane["chunks"] if lane["chunks"] else 0.0
            if lane.get("error"):
                state_text = "FAILED"
            elif lane["finished"]:
                state_text = "sealed"
            else:
                state_text = "sharding" if fraction >= 1.0 else "deriving"
            lines.append(
                f"{lane['name']:<14}{_bar(fraction):<26}"
                f"{lane['chunks_done']:>4}/{lane['chunks']:<5}  {state_text:<10}"
            )
        dashboard.draw(lines)

    results = seal_sharded_corpora_v1(specs, workers=workers, on_progress=on_progress)
    if is_tty:
        print()
    failed = []
    for spec in specs:
        name = spec["name"]
        outcome = results[name]
        if outcome["error"] is not None:
            failed.append(name)
            print(f"[seal-lanes] FAILED {name}: {outcome['error']}", file=sys.stderr, flush=True)
            continue
        index = outcome["index"]
        print(json.dumps({
            "lane": name,
            "snapshot_index": str(Path(spec["output_dir"]) / "snapshot_index.json"),
            "dataset_chunks": len(index["dataset_chunks"]),
            "shards": len(index["shards"]),
            "examples": index["examples_total"],
            "split_counts": index["split_counts"],
        }, ensure_ascii=False), flush=True)
    print(f"[seal-lanes] done in {int(time.monotonic() - started)}s  "
          f"sealed={len(specs) - len(failed)}/{len(specs)}", flush=True)
    if failed:
        print(f"[seal-lanes] 失敗したレーン: {failed}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
