"""1 レーン分の teacher 収集を起動する。

teacher に subject デッキを操縦させ、その決定を BC target として記録する。
相手はプールの高速 (<=1ms) な相手から決定的に選ぶ。
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[1] / "src")]

from mage_ptcg.meta_specialist.collect_teacher_records_v1 import run_collect_teacher_records_v1
from mage_ptcg.meta_specialist.opponent_pool_v1 import default_pool_root_v1, load_opponent_pool_v1

_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archetype-id", required=True)
    parser.add_argument("--teacher-id", required=True)
    parser.add_argument("--num-games", type=int, required=True)
    parser.add_argument("--base-seed", type=int, required=True)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--num-opponents", type=int, default=16)
    parser.add_argument("--opponent-seed", type=int, default=11)
    parser.add_argument(
        "--workers", type=int, default=1,
        help="1 レーン内で同時に走らせる対局プロセス数。局は互いに独立なので "
             "そのまま台数分速くなる。既定 1 は従来の逐次実行",
    )
    parser.add_argument("--progress-path", default="",
                        help="進捗を atomic に書く JSON。並列 supervisor が読む")
    args = parser.parse_args()

    pool = load_opponent_pool_v1(default_pool_root_v1(_ROOT))
    # 学習に使える速度階層 (<=1ms) のみ。探索相手は評価専用とする。
    fast = [
        oid for oid in sorted(pool)
        if (pool[oid].mean_decision_ms or 99.0) <= 1.0 and oid != args.teacher_id
    ]
    rng = random.Random(args.opponent_seed)
    opponents = sorted(rng.sample(fast, min(args.num_opponents, len(fast))))

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=_ROOT
    ).stdout.strip()
    manifest = run_collect_teacher_records_v1(
        progress_path=args.progress_path,
        workers=args.workers,
        archetype_id=args.archetype_id,
        subject_deck_csv_path=pool[args.teacher_id].deck_csv_path,
        teacher_id=args.teacher_id,
        opponent_ids=opponents,
        num_games=args.num_games,
        base_seed=args.base_seed,
        run_name=args.run_name,
        allowed_usages=("training-local",),
        decision_ref="docs/decisions/2026-08-05-archaludon-teacher-derivation.md",
        source_commit=commit,
    )
    print(json.dumps({k: v for k, v in manifest.items() if k != "permission_manifest"},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
